"""
Шар роботи з базою даних для MagnitApp (веб-версія бота).
Синхронний sqlite3 - Flask сам по собі синхронний, тож окремий async-шар
(як aiosqlite у Telegram-версії) тут не потрібен.
"""
import sqlite3
import os
import hashlib
import secrets
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_KYIV = ZoneInfo("Europe/Kyiv")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "magnitapp.db")


def _lower_unicode(value):
    return value.lower() if value is not None else value


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.create_function("LOWERU", 1, _lower_unicode)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def now_str():
    return datetime.now(TZ_KYIV).strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now(TZ_KYIV).date().isoformat()


# ---------------------------------------------------------------------------
# Паролі (без bcrypt/passlib - лише стандартна бібліотека Python)
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return secrets.compare_digest(digest.hex(), digest_hex)


# ---------------------------------------------------------------------------
# Ініціалізація схеми
# ---------------------------------------------------------------------------
def init_db():
    with closing(get_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('head_admin', 'admin', 'seller')),
                location TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_path TEXT,
                name TEXT,
                color TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                location TEXT NOT NULL,
                photo_hash TEXT,
                barcode TEXT,
                added_by INTEGER,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                location TEXT NOT NULL,
                price REAL NOT NULL,
                payment_method TEXT NOT NULL,
                sold_by INTEGER,
                sold_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_path TEXT,
                receipt_number TEXT NOT NULL,
                intake_date TEXT NOT NULL,
                completion_date TEXT,
                location TEXT NOT NULL,
                added_by INTEGER,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_baselines (
                location TEXT PRIMARY KEY,
                receipt_number TEXT NOT NULL,
                set_by INTEGER,
                set_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qty_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                location TEXT NOT NULL,
                old_quantity INTEGER NOT NULL,
                new_quantity INTEGER NOT NULL,
                reason TEXT,
                requested_by INTEGER,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
                requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
                decided_at TEXT,
                decided_by INTEGER
            )
            """
        )
        conn.commit()


def bootstrap_head_admin(username, password):
    """Створює першого адміна, якщо в базі взагалі немає користувачів -
    аналог ADMIN_IDS зі змінної середовища у Telegram-версії."""
    with closing(get_connection()) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]
        if count == 0:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, location) VALUES (?, ?, 'head_admin', NULL)",
                (username, hash_password(password)),
            )
            conn.commit()
            return True
    return False


# ---------------------------------------------------------------------------
# Користувачі
# ---------------------------------------------------------------------------
def get_user_by_username(username):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, username, password_hash, role, location FROM users WHERE username = ?",
            (username,),
        )
        return cur.fetchone()


def get_user_by_id(user_id):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, username, role, location FROM users WHERE id = ?", (user_id,)
        )
        return cur.fetchone()


def add_user(username, password, role, location=None):
    with closing(get_connection()) as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, location) VALUES (?, ?, ?, ?)",
                (username, hash_password(password), role, location),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def remove_user(user_id):
    with closing(get_connection()) as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ? AND role != 'head_admin'", (user_id,))
        conn.commit()
        return cur.rowcount > 0


def list_users(role=None):
    with closing(get_connection()) as conn:
        if role:
            cur = conn.execute("SELECT id, username, role, location FROM users WHERE role = ? ORDER BY username", (role,))
        else:
            cur = conn.execute("SELECT id, username, role, location FROM users ORDER BY role, username")
        return cur.fetchall()


def rename_location(old_location, new_location):
    with closing(get_connection()) as conn:
        cur1 = conn.execute("UPDATE users SET location = ? WHERE location = ?", (new_location, old_location))
        cur2 = conn.execute("UPDATE items SET location = ? WHERE location = ?", (new_location, old_location))
        conn.commit()
        return cur1.rowcount, cur2.rowcount


# ---------------------------------------------------------------------------
# Товари
# ---------------------------------------------------------------------------
def add_item(photo_path, name, color, price, quantity, location, added_by, photo_hash=None, barcode=None):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "INSERT INTO items (photo_path, name, color, price, quantity, location, photo_hash, barcode, added_by, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (photo_path, name, color, price, quantity, location, photo_hash, barcode, added_by, now_str()),
        )
        conn.commit()
        return cur.lastrowid


def get_all_items(location):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, photo_path, name, color, price, quantity, barcode FROM items WHERE location = ? ORDER BY id",
            (location,),
        )
        return cur.fetchall()


def get_item_by_id(location, item_id):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, photo_path, name, color, price, quantity, barcode, photo_hash FROM items WHERE id = ? AND location = ?",
            (item_id, location),
        )
        return cur.fetchone()


def get_item_by_barcode(location, barcode):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, photo_path, name, color, price, quantity, barcode FROM items WHERE barcode = ? AND location = ?",
            (barcode, location),
        )
        return cur.fetchone()


def get_items_with_hashes(location):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, photo_path, name, color, price, quantity, photo_hash FROM items "
            "WHERE location = ? AND quantity > 0 AND photo_hash IS NOT NULL",
            (location,),
        )
        return cur.fetchall()


def find_items_by_query(location, query):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, photo_path, name, color, price, quantity, barcode FROM items "
            "WHERE location = ? AND (LOWERU(color) LIKE LOWERU(?) OR LOWERU(name) LIKE LOWERU(?)) ORDER BY id",
            (location, f"%{query}%", f"%{query}%"),
        )
        return cur.fetchall()


def update_quantity(location, item_id, new_quantity):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "UPDATE items SET quantity = ? WHERE id = ? AND location = ?", (new_quantity, item_id, location)
        )
        conn.commit()
        return cur.rowcount > 0


def add_quantity(location, item_id, delta):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "UPDATE items SET quantity = quantity + ? WHERE id = ? AND location = ?",
            (delta, item_id, location),
        )
        conn.commit()
        return cur.rowcount > 0


def update_price_by_barcode(barcode, new_price):
    with closing(get_connection()) as conn:
        cur = conn.execute("UPDATE items SET price = ? WHERE barcode = ?", (new_price, barcode))
        conn.commit()
        return cur.rowcount


def delete_item(location, item_id):
    with closing(get_connection()) as conn:
        cur = conn.execute("DELETE FROM items WHERE id = ? AND location = ?", (item_id, location))
        conn.commit()
        return cur.rowcount > 0


def count_items_by_location(location):
    with closing(get_connection()) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM items WHERE location = ?", (location,))
        return cur.fetchone()[0]


def delete_all_items_by_location(location):
    with closing(get_connection()) as conn:
        cur = conn.execute("DELETE FROM items WHERE location = ?", (location,))
        conn.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# Продажі
# ---------------------------------------------------------------------------
def finalize_sale(location, item_id, price, payment_method, user_id):
    """Атомарна операція: списати одиницю товару і зафіксувати продаж -
    або обидві дії відбудуться, або жодна."""
    with closing(get_connection()) as conn:
        try:
            cur = conn.execute(
                "UPDATE items SET quantity = quantity - 1 WHERE id = ? AND location = ? AND quantity > 0",
                (item_id, location),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return None
            conn.execute(
                "INSERT INTO sales (item_id, location, price, payment_method, sold_by, sold_at) VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, location, price, payment_method, user_id, now_str()),
            )
            conn.commit()
            cur2 = conn.execute("SELECT quantity FROM items WHERE id = ?", (item_id,))
            row = cur2.fetchone()
            return row[0] if row else None
        except Exception:
            conn.rollback()
            raise


def get_today_sales_totals(location):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT payment_method, COUNT(*), COALESCE(SUM(price), 0) FROM sales "
            "WHERE location = ? AND date(sold_at) = date(?) GROUP BY payment_method",
            (location, today_str()),
        )
        return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def get_sales_rows(location=None, start_date=None, end_date=None):
    query = (
        "SELECT sales.sold_at, sales.location, items.name, items.color, sales.price, sales.payment_method "
        "FROM sales LEFT JOIN items ON items.id = sales.item_id WHERE 1=1"
    )
    params = []
    if location:
        query += " AND sales.location = ?"
        params.append(location)
    if start_date:
        query += " AND date(sales.sold_at) >= date(?)"
        params.append(start_date)
    if end_date:
        query += " AND date(sales.sold_at) <= date(?)"
        params.append(end_date)
    query += " ORDER BY sales.sold_at"
    with closing(get_connection()) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Ремонти
# ---------------------------------------------------------------------------
def parse_receipt_number(receipt_number):
    parts = receipt_number.split("-", 1)
    if len(parts) != 2:
        return None
    book, seq = parts[0].strip(), parts[1].strip()
    if not (book.isdigit() and seq.isdigit()):
        return None
    return book, int(seq)


def add_repair(photo_path, receipt_number, intake_date, location, added_by):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "INSERT INTO repairs (photo_path, receipt_number, intake_date, location, added_by, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (photo_path, receipt_number, intake_date, location, added_by, now_str()),
        )
        conn.commit()
        return cur.lastrowid


def get_last_repair_for_location(location):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT receipt_number FROM repairs WHERE location = ? ORDER BY id DESC LIMIT 1", (location,)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur = conn.execute("SELECT receipt_number FROM repair_baselines WHERE location = ?", (location,))
        row = cur.fetchone()
        return row[0] if row else None


def set_repair_baseline(location, receipt_number, set_by):
    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT INTO repair_baselines (location, receipt_number, set_by, set_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(location) DO UPDATE SET receipt_number=excluded.receipt_number, set_by=excluded.set_by, set_at=excluded.set_at",
            (location, receipt_number, set_by, now_str()),
        )
        conn.commit()


def get_pending_repairs(location):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, photo_path, receipt_number, intake_date FROM repairs "
            "WHERE location = ? AND completion_date IS NULL ORDER BY id",
            (location,),
        )
        return cur.fetchall()


def mark_repair_completed(repair_id, completion_date):
    with closing(get_connection()) as conn:
        conn.execute("UPDATE repairs SET completion_date = ? WHERE id = ?", (completion_date, repair_id))
        conn.commit()


def delete_repair(repair_id, location):
    """Видалення квитанції з ремонту (наприклад, помилково внесена).
    Обмежено тією ж точкою, щоб продавець не міг видалити чужу."""
    with closing(get_connection()) as conn:
        cur = conn.execute("DELETE FROM repairs WHERE id = ? AND location = ?", (repair_id, location))
        conn.commit()
        return cur.rowcount > 0


def get_repair_by_id(repair_id):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, photo_path, receipt_number, intake_date, location FROM repairs WHERE id = ?", (repair_id,)
        )
        return cur.fetchone()


def get_repairs_by_period(location, start_date, end_date):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, photo_path, receipt_number, intake_date, completion_date FROM repairs "
            "WHERE location = ? AND date(intake_date) >= date(?) AND date(intake_date) <= date(?) "
            "ORDER BY date(intake_date)",
            (location, start_date, end_date),
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Web Push - підписки
# ---------------------------------------------------------------------------
def add_push_subscription(user_id, endpoint, p256dh, auth):
    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth",
            (user_id, endpoint, p256dh, auth, now_str()),
        )
        conn.commit()


def remove_push_subscription(endpoint):
    with closing(get_connection()) as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        conn.commit()


def get_admin_push_subscriptions():
    """Підписки всіх адмінів (head_admin + admin) - їм надсилаємо сповіщення."""
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT ps.id, ps.endpoint, ps.p256dh, ps.auth FROM push_subscriptions ps "
            "JOIN users u ON u.id = ps.user_id WHERE u.role IN ('admin', 'head_admin')"
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Запити на зміну кількості (продавець -> потребує підтвердження адміна)
# ---------------------------------------------------------------------------
def create_qty_request(item_id, location, old_quantity, new_quantity, reason, requested_by):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "INSERT INTO qty_requests (item_id, location, old_quantity, new_quantity, reason, requested_by, requested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, location, old_quantity, new_quantity, reason, requested_by, now_str()),
        )
        conn.commit()
        return cur.lastrowid


def get_pending_qty_requests(location=None):
    with closing(get_connection()) as conn:
        if location:
            cur = conn.execute(
                "SELECT qr.id, qr.item_id, qr.location, qr.old_quantity, qr.new_quantity, qr.reason, qr.requested_at, "
                "items.name, items.color FROM qty_requests qr LEFT JOIN items ON items.id = qr.item_id "
                "WHERE qr.status = 'pending' AND qr.location = ? ORDER BY qr.requested_at",
                (location,),
            )
        else:
            cur = conn.execute(
                "SELECT qr.id, qr.item_id, qr.location, qr.old_quantity, qr.new_quantity, qr.reason, qr.requested_at, "
                "items.name, items.color FROM qty_requests qr LEFT JOIN items ON items.id = qr.item_id "
                "WHERE qr.status = 'pending' ORDER BY qr.requested_at"
            )
        return cur.fetchall()


def get_qty_request_by_id(request_id):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, item_id, location, old_quantity, new_quantity, reason, status FROM qty_requests WHERE id = ?",
            (request_id,),
        )
        return cur.fetchone()


def decide_qty_request(request_id, approve: bool, decided_by):
    with closing(get_connection()) as conn:
        request_row = conn.execute(
            "SELECT item_id, location, new_quantity, status FROM qty_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if request_row is None or request_row[3] != "pending":
            return False
        item_id, location, new_quantity, _ = request_row
        status = "approved" if approve else "rejected"
        conn.execute(
            "UPDATE qty_requests SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
            (status, now_str(), decided_by, request_id),
        )
        if approve:
            conn.execute(
                "UPDATE items SET quantity = ? WHERE id = ? AND location = ?", (new_quantity, item_id, location)
            )
        conn.commit()
        return True


# ---------------------------------------------------------------------------
# Переоблік
# ---------------------------------------------------------------------------
def get_items_for_recount(location):
    with closing(get_connection()) as conn:
        cur = conn.execute(
            "SELECT id, photo_path, name, color, price, quantity FROM items WHERE location = ? ORDER BY id",
            (location,),
        )
        return cur.fetchall()


def apply_recount(location, counted_quantities, counted_by):
    """counted_quantities: список (item_id, актуальна_кількість).
    Повертає список змін (item_id, назва, стара, нова) - лише де щось змінилось."""
    changes = []
    with closing(get_connection()) as conn:
        for item_id, actual_qty in counted_quantities:
            row = conn.execute(
                "SELECT name, color, quantity FROM items WHERE id = ? AND location = ?", (item_id, location)
            ).fetchone()
            if row is None:
                continue
            name, color, old_qty = row
            if old_qty != actual_qty:
                conn.execute(
                    "UPDATE items SET quantity = ? WHERE id = ? AND location = ?", (actual_qty, item_id, location)
                )
                changes.append({"item_id": item_id, "name": name, "color": color, "old": old_qty, "new": actual_qty})
        conn.commit()
    return changes

