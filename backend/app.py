"""
MagnitApp - веб-застосунок (PWA) для обліку товарів на точках продажу.
Повна заміна Telegram-бота: та сама бізнес-логіка (штрихкод, порівняння
фото, продаж, прихід, переоблік, ремонти, звіти), але власний backend
(Flask) і власний фронтенд (PWA) замість Telegram.

Запуск:
    export JWT_SECRET="якийсь-довгий-випадковий-рядок"
    export HEAD_ADMIN_USERNAME="admin"
    export HEAD_ADMIN_PASSWORD="змінити-цей-пароль"
    python3 app.py
"""
import os
import logging
import io
import functools
from datetime import datetime, timedelta

import jwt
from flask import Flask, request, jsonify, send_from_directory, send_file, g

import db
import imaging
import vapid
import push
import reports

app = Flask(__name__, static_folder=None)

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-insecure-secret-change-me")
JWT_ALGO = "HS256"
TOKEN_TTL_HOURS = 24 * 30  # токен дійсний місяць, щоб не переlogin-иватись щодня

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")


# ---------------------------------------------------------------------------
# Аутентифікація
# ---------------------------------------------------------------------------
def make_token(user_id, username, role, location):
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "location": location,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_token(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        return None


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = request.args.get("token")  # для посилань на завантаження файлів (Excel)
        if not token:
            return jsonify({"error": "Потрібна авторизація"}), 401
        payload = decode_token(token)
        if payload is None:
            return jsonify({"error": "Токен недійсний або застарів"}), 401
        g.user = payload
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if g.user["role"] not in ("admin", "head_admin"):
            return jsonify({"error": "Доступно лише адміну"}), 403
        return f(*args, **kwargs)
    return wrapper


def head_admin_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if g.user["role"] != "head_admin":
            return jsonify({"error": "Доступно лише головному адміну"}), 403
        return f(*args, **kwargs)
    return wrapper


def seller_location(user):
    """Точка продавця; для адміна/головного адміна повертає None (вони
    мають вказувати location параметром запиту)."""
    return user.get("location")


def notify_admins_push(title, body, url=None):
    """Надсилає push-сповіщення усім адмінам одразу (аналог розсилки
    повідомлень усім адмінам у Telegram-версії).

    Навмисно "ковтає" будь-яку помилку - сповіщення другорядні, і якщо
    надсилання не вдалось (наприклад, pywebpush ще не встановлено, чи
    тимчасова мережева помилка), основна дія користувача (зміна кількості,
    прийняття квитанції тощо) все одно має завершитись успішно."""
    try:
        subs = db.get_admin_push_subscriptions()
    except Exception as e:
        logging.getLogger(__name__).error(f"Не вдалось отримати підписки на push: {e}")
        return
    for sub_id, endpoint, p256dh, auth in subs:
        try:
            subscription_info = {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}
            result = push.send_push_notification(subscription_info, title, body, url)
            if result is False:
                db.remove_push_subscription(endpoint)
        except Exception as e:
            logging.getLogger(__name__).error(f"Помилка надсилання push адміну {sub_id}: {e}")


@app.route("/api/push/public-key", methods=["GET"])
def push_public_key():
    return jsonify({"publicKey": vapid.get_public_key_b64()})


@app.route("/api/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    data = request.get_json(force=True)
    db.add_push_subscription(
        g.user["user_id"], data["endpoint"], data["keys"]["p256dh"], data["keys"]["auth"]
    )
    return jsonify({"ok": True})


@app.route("/api/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe():
    data = request.get_json(force=True)
    db.remove_push_subscription(data["endpoint"])
    return jsonify({"ok": True})


@app.route("/api/push/test", methods=["POST"])
@login_required
@admin_required
def push_test():
    subs = db.get_admin_push_subscriptions()
    if not subs:
        return jsonify({"error": "Немає жодної підписки на сповіщення - спершу увімкніть їх кнопкою на головному екрані"}), 400
    if not push.PYWEBPUSH_AVAILABLE:
        return jsonify({"error": "На сервері не встановлено бібліотеку pywebpush (pip install pywebpush)"}), 500
    sent, failed = 0, 0
    for sub_id, endpoint, p256dh, auth in subs:
        subscription_info = {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}
        result = push.send_push_notification(subscription_info, "🔔 Тестове сповіщення", "Якщо ви це бачите - усе працює!")
        if result is True:
            sent += 1
        else:
            failed += 1
            if result is False:
                db.remove_push_subscription(endpoint)
    return jsonify({"sent": sent, "failed": failed, "total_subscriptions": len(subs)})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    row = db.get_user_by_username(username)
    if row is None:
        return jsonify({"error": "Невірний логін або пароль"}), 401
    user_id, uname, password_hash, role, location = row
    if not db.verify_password(password, password_hash):
        return jsonify({"error": "Невірний логін або пароль"}), 401
    token = make_token(user_id, uname, role, location)
    return jsonify({"token": token, "role": role, "location": location, "username": uname})


@app.route("/api/me", methods=["GET"])
@login_required
def me():
    return jsonify(g.user)


# ---------------------------------------------------------------------------
# Допоміжне: збереження завантаженого фото
# ---------------------------------------------------------------------------
def save_uploaded_photo(file_storage):
    import uuid
    ext = os.path.splitext(file_storage.filename or "photo.jpg")[1] or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    file_storage.save(path)
    return filename


def photo_url(filename):
    return f"/api/photos/{filename}" if filename else None


@app.route("/api/photos/<path:filename>")
def serve_photo(filename):
    return send_from_directory(UPLOAD_DIR, filename)


def item_to_dict(row):
    item_id, photo_path, name, color, price, quantity, barcode = row
    return {
        "id": item_id, "photo_url": photo_url(photo_path), "name": name,
        "color": color, "price": price, "quantity": quantity, "barcode": barcode,
    }


# ---------------------------------------------------------------------------
# Товари: розпізнавання (фото -> штрихкод / схожість), продаж, прихід
# ---------------------------------------------------------------------------
@app.route("/api/items/identify", methods=["POST"])
@login_required
def identify_item():
    """Приймає фото, повертає: знайдений точний збіг (за штрихкодом),
    список схожих за фото, або "нічого не знайдено" - разом з обчисленим
    хешем/штрихкодом, щоб фронтенд міг використати їх при створенні нового товару."""
    location = request.form.get("location") or seller_location(g.user)
    if not location:
        return jsonify({"error": "Не вказано торгову точку"}), 400
    photo = request.files.get("photo")
    if photo is None:
        return jsonify({"error": "Немає фото"}), 400

    photo_bytes = photo.read()
    barcode = imaging.detect_barcodes(photo_bytes)

    if barcode:
        exact = db.get_item_by_barcode(location, barcode)
        if exact:
            return jsonify({"status": "exact_match", "item": item_to_dict(exact), "barcode": barcode})

    photo_hash = imaging.compute_image_hash(photo_bytes)
    items_with_hashes = db.get_items_with_hashes(location)
    matches = imaging.find_close_matches(items_with_hashes, photo_hash)

    filename = save_uploaded_photo(photo)

    if matches:
        return jsonify({
            "status": "possible_matches",
            "matches": [
                {"id": m[0], "photo_url": photo_url(m[1]), "name": m[2], "color": m[3], "price": m[4], "quantity": m[5]}
                for m in matches
            ],
            "photo_filename": filename,
            "photo_hash": photo_hash,
            "barcode": barcode,
        })

    return jsonify({
        "status": "not_found",
        "photo_filename": filename,
        "photo_hash": photo_hash,
        "barcode": barcode,
    })


@app.route("/api/items", methods=["POST"])
@login_required
def create_item():
    data = request.get_json(force=True)
    location = data.get("location") or seller_location(g.user)
    if not location:
        return jsonify({"error": "Не вказано торгову точку"}), 400
    item_id = db.add_item(
        photo_path=data.get("photo_filename"),
        name=data.get("name"),
        color=data["color"],
        price=float(data["price"]),
        quantity=int(data["quantity"]),
        location=location,
        added_by=g.user["user_id"],
        photo_hash=data.get("photo_hash"),
        barcode=data.get("barcode"),
    )
    return jsonify({"id": item_id})


@app.route("/api/items", methods=["GET"])
@login_required
def list_items():
    location = request.args.get("location") or seller_location(g.user)
    if not location:
        return jsonify({"error": "Не вказано торгову точку"}), 400
    query = request.args.get("query")
    rows = db.find_items_by_query(location, query) if query else db.get_all_items(location)
    return jsonify([item_to_dict(r) for r in rows])


@app.route("/api/items/<int:item_id>", methods=["PATCH"])
@login_required
def update_item(item_id):
    location = request.args.get("location") or seller_location(g.user)
    data = request.get_json(force=True)
    if "quantity" not in data:
        return jsonify({"ok": True})

    new_quantity = int(data["quantity"])

    if g.user["role"] in ("admin", "head_admin"):
        # Адмін має пряме право змінювати кількість без підтвердження.
        db.update_quantity(location, item_id, new_quantity)
        return jsonify({"ok": True, "direct": True})

    # Продавець - створюємо запит, який має підтвердити адмін (як у боті).
    reason = (data.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "Потрібно вказати причину зміни кількості"}), 400
    item = db.get_item_by_id(location, item_id)
    if item is None:
        return jsonify({"error": "Товар не знайдено"}), 404
    old_quantity = item[5]
    request_id = db.create_qty_request(item_id, location, old_quantity, new_quantity, reason, g.user["user_id"])

    notify_admins_push(
        "✏️ Запит на зміну залишку",
        f"{g.user['username']} ({location}): {item[2]} {old_quantity} → {new_quantity}",
    )
    return jsonify({"ok": True, "direct": False, "request_id": request_id})


@app.route("/api/qty-requests/pending", methods=["GET"])
@login_required
@admin_required
def pending_qty_requests_route():
    location = request.args.get("location")
    rows = db.get_pending_qty_requests(location)
    return jsonify([
        {
            "id": r[0], "item_id": r[1], "location": r[2], "old_quantity": r[3],
            "new_quantity": r[4], "reason": r[5], "requested_at": r[6],
            "name": r[7], "color": r[8],
        }
        for r in rows
    ])


@app.route("/api/qty-requests/<int:request_id>/approve", methods=["POST"])
@login_required
@admin_required
def approve_qty_request_route(request_id):
    ok = db.decide_qty_request(request_id, True, g.user["user_id"])
    return jsonify({"ok": ok})


@app.route("/api/qty-requests/<int:request_id>/reject", methods=["POST"])
@login_required
@admin_required
def reject_qty_request_route(request_id):
    ok = db.decide_qty_request(request_id, False, g.user["user_id"])
    return jsonify({"ok": ok})


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
@login_required
def remove_item(item_id):
    location = request.args.get("location") or seller_location(g.user)
    ok = db.delete_item(location, item_id)
    return jsonify({"ok": ok})


@app.route("/api/items/price-by-barcode", methods=["POST"])
@login_required
@admin_required
def edit_price_by_barcode():
    data = request.get_json(force=True)
    count = db.update_price_by_barcode(data["barcode"], float(data["price"]))
    return jsonify({"updated_count": count})


@app.route("/api/items/detect-barcode-for-price", methods=["POST"])
@login_required
@admin_required
def detect_barcode_for_price():
    """Для кнопки «Змінити ціну» - лише читає штрихкод з фото (без прив'язки
    до точки, бо ціна за штрихкодом змінюється одразу на всіх точках)."""
    photo = request.files.get("photo")
    if photo is None:
        return jsonify({"error": "Немає фото"}), 400
    photo_bytes = photo.read()
    barcode = imaging.detect_barcodes(photo_bytes)
    if not barcode:
        return jsonify({"barcode": None, "item": None})
    item = db.get_item_by_barcode_any_location(barcode)
    item_dict = None
    if item:
        item_id, photo_path, name, color, price, quantity, location = item
        item_dict = {"id": item_id, "photo_url": photo_url(photo_path), "name": name, "color": color,
                     "price": price, "quantity": quantity, "location": location}
    return jsonify({"barcode": barcode, "item": item_dict})


@app.route("/api/sales", methods=["POST"])
@login_required
def create_sale():
    data = request.get_json(force=True)
    location = data.get("location") or seller_location(g.user)
    item_id = int(data["item_id"])
    payment_method = data["payment_method"]
    item = db.get_item_by_id(location, item_id)
    if item is None:
        return jsonify({"error": "Товар не знайдено"}), 404
    price = item[4]
    new_qty = db.finalize_sale(location, item_id, price, payment_method, g.user["user_id"])
    if new_qty is None:
        return jsonify({"error": "Товару вже немає в наявності"}), 409
    return jsonify({"ok": True, "new_quantity": new_qty})


@app.route("/api/sales/supply", methods=["POST"])
@login_required
def supply_add():
    """Прихід товару: додати кількість до вже наявного товару."""
    data = request.get_json(force=True)
    location = data.get("location") or seller_location(g.user)
    item_id = int(data["item_id"])
    delta = int(data["quantity"])
    ok = db.add_quantity(location, item_id, delta)
    return jsonify({"ok": ok})


# ---------------------------------------------------------------------------
# Звіти продажів
# ---------------------------------------------------------------------------
@app.route("/api/sales/today", methods=["GET"])
@login_required
def sales_today():
    location = request.args.get("location") or seller_location(g.user)
    totals = db.get_today_sales_totals(location)
    cash_count, cash_sum = totals.get("Готівка", (0, 0))
    noncash_count, noncash_sum = totals.get("Безготівка", (0, 0))
    return jsonify({
        "cash": {"count": cash_count, "sum": cash_sum},
        "noncash": {"count": noncash_count, "sum": noncash_sum},
        "total": {"count": cash_count + noncash_count, "sum": cash_sum + noncash_sum},
    })


@app.route("/api/sales/report", methods=["GET"])
@login_required
def sales_report():
    location = request.args.get("location") or seller_location(g.user)
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_sales_rows(location=location, start_date=start, end_date=end)
    return jsonify([
        {"sold_at": r[0], "location": r[1], "name": r[2], "color": r[3], "price": r[4], "payment_method": r[5]}
        for r in rows
    ])


@app.route("/api/sales/report-all", methods=["GET"])
@login_required
@admin_required
def sales_report_all():
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_sales_rows(location=None, start_date=start, end_date=end)
    return jsonify([
        {"sold_at": r[0], "location": r[1], "name": r[2], "color": r[3], "price": r[4], "payment_method": r[5]}
        for r in rows
    ])


@app.route("/api/sales/report.xlsx", methods=["GET"])
@login_required
def sales_report_xlsx():
    location = request.args.get("location") or seller_location(g.user)
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_sales_rows(location=location, start_date=start, end_date=end)
    buf = reports.build_sales_excel(rows, include_location_column=not location)
    filename = f"продажі_{location or 'всі_точки'}_{start}_{end}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/sales/report-all.xlsx", methods=["GET"])
@login_required
@admin_required
def sales_report_all_xlsx():
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_sales_rows(location=None, start_date=start, end_date=end)
    buf = reports.build_sales_excel(rows, include_location_column=True)
    filename = f"продажі_всі_точки_{start}_{end}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# Продавці / адміни (керування користувачами)
# ---------------------------------------------------------------------------
@app.route("/api/sellers", methods=["GET"])
@login_required
@admin_required
def list_sellers_route():
    rows = db.list_users(role="seller")
    return jsonify([{"id": r[0], "username": r[1], "location": r[3]} for r in rows])


@app.route("/api/sellers", methods=["POST"])
@login_required
@admin_required
def add_seller_route():
    data = request.get_json(force=True)
    ok = db.add_user(data["username"], data["password"], "seller", data["location"])
    if not ok:
        return jsonify({"error": "Такий логін вже існує"}), 409
    return jsonify({"ok": True})


@app.route("/api/sellers/<int:user_id>", methods=["DELETE"])
@login_required
@admin_required
def remove_seller_route(user_id):
    ok = db.remove_user(user_id)
    return jsonify({"ok": ok})


@app.route("/api/admins", methods=["GET"])
@login_required
@head_admin_required
def list_admins_route():
    rows = db.list_users(role="admin")
    return jsonify([{"id": r[0], "username": r[1]} for r in rows])


@app.route("/api/admins", methods=["POST"])
@login_required
@head_admin_required
def add_admin_route():
    data = request.get_json(force=True)
    ok = db.add_user(data["username"], data["password"], "admin", None)
    if not ok:
        return jsonify({"error": "Такий логін вже існує"}), 409
    return jsonify({"ok": True})


@app.route("/api/admins/<int:user_id>", methods=["DELETE"])
@login_required
@head_admin_required
def remove_admin_route(user_id):
    ok = db.remove_user(user_id)
    return jsonify({"ok": ok})


# ---------------------------------------------------------------------------
# Точки продажу
# ---------------------------------------------------------------------------
@app.route("/api/locations", methods=["GET"])
@login_required
def list_locations_route():
    rows = db.list_users(role="seller")
    locations = sorted({r[3] for r in rows if r[3]})
    return jsonify(locations)


@app.route("/api/locations/rename", methods=["POST"])
@login_required
@admin_required
def rename_location_route():
    data = request.get_json(force=True)
    users_count, items_count = db.rename_location(data["old_location"], data["new_location"])
    return jsonify({"users_updated": users_count, "items_updated": items_count})


@app.route("/api/locations/<path:location>/clear", methods=["DELETE"])
@login_required
@admin_required
def clear_location_route(location):
    count = db.delete_all_items_by_location(location)
    return jsonify({"deleted_count": count})


@app.route("/api/locations/<path:location>/count", methods=["GET"])
@login_required
@admin_required
def count_location_route(location):
    return jsonify({"count": db.count_items_by_location(location)})


# ---------------------------------------------------------------------------
# Ремонти
# ---------------------------------------------------------------------------
@app.route("/api/repairs", methods=["POST"])
@login_required
def create_repair():
    location = request.form.get("location") or seller_location(g.user)
    receipt_number = request.form.get("receipt_number")
    intake_date = request.form.get("intake_date")
    photo = request.files.get("photo")
    filename = save_uploaded_photo(photo) if photo else None

    previous_number = db.get_last_repair_for_location(location)
    gap_warning = None
    if previous_number:
        parsed_prev = db.parse_receipt_number(previous_number)
        parsed_new = db.parse_receipt_number(receipt_number)
        if parsed_prev and parsed_new and parsed_prev[0] == parsed_new[0]:
            if parsed_new[1] - parsed_prev[1] > 1:
                gap_warning = (
                    f"Можливо пропущено квитанції на точці «{location}»! "
                    f"Попередній номер: {previous_number}, новий: {receipt_number}, "
                    f"пропущено {parsed_new[1] - parsed_prev[1] - 1}."
                )

    repair_id = db.add_repair(filename, receipt_number, intake_date, location, g.user["user_id"])
    if gap_warning:
        notify_admins_push("⚠️ Пропуск квитанції", gap_warning)
    return jsonify({"id": repair_id, "gap_warning": gap_warning})


@app.route("/api/repairs/baseline", methods=["POST"])
@login_required
def set_repair_baseline_route():
    data = request.get_json(force=True)
    location = data.get("location") or seller_location(g.user)
    db.set_repair_baseline(location, data["receipt_number"], g.user["user_id"])
    return jsonify({"ok": True})


@app.route("/api/repairs/pending", methods=["GET"])
@login_required
def pending_repairs_route():
    location = request.args.get("location") or seller_location(g.user)
    rows = db.get_pending_repairs(location)
    return jsonify([
        {"id": r[0], "photo_url": photo_url(r[1]), "receipt_number": r[2], "intake_date": r[3]}
        for r in rows
    ])


@app.route("/api/repairs/<int:repair_id>/complete", methods=["POST"])
@login_required
def complete_repair_route(repair_id):
    data = request.get_json(force=True)
    cost = data.get("cost")
    cost = float(cost) if cost not in (None, "") else None
    payment_method = data.get("payment_method")
    db.mark_repair_completed(repair_id, data["completion_date"], cost, payment_method)
    return jsonify({"ok": True})


@app.route("/api/repairs/<int:repair_id>", methods=["DELETE"])
@login_required
def delete_repair_route(repair_id):
    location = request.args.get("location") or seller_location(g.user)

    if g.user["role"] in ("admin", "head_admin"):
        ok = db.delete_repair(repair_id, location)
        return jsonify({"ok": ok, "direct": True})

    # Продавець - лише запит, який має підтвердити адмін.
    repair = db.get_repair_by_id(repair_id)
    if repair is None:
        return jsonify({"error": "Квитанцію не знайдено"}), 404
    receipt_number = repair[2]
    request_id = db.create_repair_delete_request(repair_id, location, receipt_number, g.user["user_id"])
    notify_admins_push(
        "🗑️ Запит на видалення квитанції",
        f"{g.user['username']} ({location}): квитанція №{receipt_number}",
    )
    return jsonify({"ok": True, "direct": False, "request_id": request_id})


@app.route("/api/repair-delete-requests/pending", methods=["GET"])
@login_required
@admin_required
def pending_repair_delete_requests_route():
    location = request.args.get("location")
    rows = db.get_pending_repair_delete_requests(location)
    return jsonify([
        {"id": r[0], "repair_id": r[1], "location": r[2], "receipt_number": r[3], "requested_at": r[4]}
        for r in rows
    ])


@app.route("/api/repair-delete-requests/<int:request_id>/approve", methods=["POST"])
@login_required
@admin_required
def approve_repair_delete_request_route(request_id):
    ok = db.decide_repair_delete_request(request_id, True, g.user["user_id"])
    return jsonify({"ok": ok})


@app.route("/api/repair-delete-requests/<int:request_id>/reject", methods=["POST"])
@login_required
@admin_required
def reject_repair_delete_request_route(request_id):
    ok = db.decide_repair_delete_request(request_id, False, g.user["user_id"])
    return jsonify({"ok": ok})


@app.route("/api/repairs/report", methods=["GET"])
@login_required
def repairs_report_route():
    location = request.args.get("location") or seller_location(g.user)
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_repairs_by_period(location, start, end)
    return jsonify([
        {
            "id": r[0], "photo_url": photo_url(r[1]), "receipt_number": r[2],
            "intake_date": r[3], "completion_date": r[4], "cost": r[5], "payment_method": r[6],
        }
        for r in rows
    ])


@app.route("/api/repairs/report.xlsx", methods=["GET"])
@login_required
def repairs_report_xlsx():
    location = request.args.get("location") or seller_location(g.user)
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_repairs_by_period(location, start, end)
    buf = reports.build_repairs_excel(rows)
    filename = f"ремонти_{location}_{start}_{end}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# Переоблік
# ---------------------------------------------------------------------------
@app.route("/api/recount/items", methods=["GET"])
@login_required
def recount_items_route():
    location = request.args.get("location") or seller_location(g.user)
    rows = db.get_items_for_recount(location)
    return jsonify([
        {"id": r[0], "photo_url": photo_url(r[1]), "name": r[2], "color": r[3], "price": r[4], "quantity": r[5]}
        for r in rows
    ])


@app.route("/api/recount/apply", methods=["POST"])
@login_required
def recount_apply_route():
    location = request.args.get("location") or seller_location(g.user)
    data = request.get_json(force=True)
    counted = [(int(c["id"]), int(c["quantity"])) for c in data["items"]]
    changes = db.apply_recount(location, counted, g.user["user_id"])
    if changes:
        summary = "; ".join(f"{c['name']} {c['old']}→{c['new']}" for c in changes)
        notify_admins_push("🔄 Переоблік завершено", f"{location}: {summary}"[:200])
    return jsonify({"changes": changes})


# ---------------------------------------------------------------------------
# Роздача фронтенду (PWA)
# ---------------------------------------------------------------------------
@app.route("/")
def serve_index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>")
def serve_frontend(path):
    return send_from_directory(FRONTEND_DIR, path)


# Ініціалізація бази виконується одразу при завантаженні модуля - це потрібно,
# щоб працювало і при локальному запуску (python3 app.py), і на хостингу
# через gunicorn (gunicorn імпортує сам модуль, не викликаючи __main__).
db.init_db()
_head_username = os.environ.get("HEAD_ADMIN_USERNAME")
_head_password = os.environ.get("HEAD_ADMIN_PASSWORD")
if _head_username and _head_password:
    if db.bootstrap_head_admin(_head_username, _head_password):
        print(f"Створено головного адміна: {_head_username}")


def main():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)


if __name__ == "__main__":
    main()
