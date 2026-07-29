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
import vapid
import push
import reports

app = Flask(__name__, static_folder=None)

JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError(
        "Змінна середовища JWT_SECRET не встановлена. Запуск без неї небезпечний: "
        "усі токени входу підписувались би відомим усім значенням із коду, і будь-хто "
        "міг би підробити токен адміна. Встановіть JWT_SECRET (довгий випадковий рядок) "
        "перед запуском, наприклад: export JWT_SECRET=\"$(python3 -c 'import secrets; print(secrets.token_hex(32))')\""
    )
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


def parts_or_admin_required(f):
    """Доступно звичайним адмінам, головному адміну, І обмеженому
    'адміну запчастин' (parts_admin) - для двох конкретних функцій:
    підтвердження запчастин і контроль видалення квитанцій ремонту."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if g.user["role"] not in ("admin", "head_admin", "parts_admin"):
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
    return jsonify({"publicKey": vapid.get_public_key_b64(), "usingEnvKeys": vapid.using_env_keys()})


@app.route("/api/settings/generate-vapid-keys", methods=["POST"])
@login_required
@head_admin_required
def generate_vapid_keys_route():
    """Генерує НОВУ пару VAPID-ключів для копіювання у змінні середовища
    Render - нічого не зберігає на сервері, лише повертає для копіювання."""
    private_b64, public_b64 = vapid.generate_env_keys()
    return jsonify({"VAPID_PRIVATE_KEY": private_b64, "VAPID_PUBLIC_KEY": public_b64})


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


# ---------------------------------------------------------------------------
# Обмеження спроб входу (проти підбору пароля).
# Просте рішення в пам'яті процесу: не переживає рестарт і не ділиться станом
# між кількома worker-ами gunicorn. Для 4 точок з невеликою командою цього
# достатньо; для більшого навантаження варто перенести лічильники в Redis.
# ---------------------------------------------------------------------------
_LOGIN_ATTEMPTS = {}  # key -> [timestamps of failed attempts]
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60


def _login_rate_limit_key():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    username = (request.get_json(force=True, silent=True) or {}).get("username", "")
    return f"{ip}:{username.strip().lower()}"


def _is_login_rate_limited(key):
    now = datetime.utcnow().timestamp()
    attempts = [t for t in _LOGIN_ATTEMPTS.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[key] = attempts
    return len(attempts) >= LOGIN_MAX_ATTEMPTS


def _register_failed_login(key):
    _LOGIN_ATTEMPTS.setdefault(key, []).append(datetime.utcnow().timestamp())


@app.route("/api/login", methods=["POST"])
def login():
    rate_key = _login_rate_limit_key()
    if _is_login_rate_limited(rate_key):
        return jsonify({
            "error": f"Забагато невдалих спроб входу. Спробуйте ще раз за {LOGIN_WINDOW_SECONDS // 60} хв."
        }), 429

    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    row = db.get_user_by_username(username)
    if row is None:
        _register_failed_login(rate_key)
        return jsonify({"error": "Невірний логін або пароль"}), 401
    user_id, uname, password_hash, role, location = row
    if not db.verify_password(password, password_hash):
        _register_failed_login(rate_key)
        return jsonify({"error": "Невірний логін або пароль"}), 401
    _LOGIN_ATTEMPTS.pop(rate_key, None)  # успішний вхід - скидаємо лічильник

    # Підтвердження нового пристрою - лише для продавців. Замість того, щоб
    # "вибивати" попередній сеанс, новий пристрій потребує підтвердження
    # головного адміна (сеанс на старому пристрої продовжує працювати).
    if role == "seller":
        device_id = (data.get("device_id") or "").strip()
        if not device_id:
            return jsonify({"error": "Пристрій не розпізнано, оновіть застосунок"}), 400
        if not db.is_device_approved(user_id, device_id):
            existing = db.get_pending_login_request(user_id, device_id)
            if existing:
                return jsonify({"status": "pending", "request_id": existing[0],
                                 "error": "Очікує підтвердження головного адміна"}), 202
            device_info = (data.get("device_info") or request.headers.get("User-Agent", ""))[:200]
            request_id = db.create_login_request(user_id, uname, device_id, device_info)
            notify_admins_push(
                "🔐 Новий пристрій продавця",
                f"{uname} намагається увійти з нового пристрою - потрібне підтвердження",
            )
            return jsonify({"status": "pending", "request_id": request_id,
                             "error": "Новий пристрій - надіслано запит головному адміну на підтвердження"}), 202

    token = make_token(user_id, uname, role, location)
    return jsonify({"token": token, "role": role, "location": location, "username": uname})


@app.route("/api/login-requests/pending", methods=["GET"])
@login_required
@head_admin_required
def pending_login_requests_route():
    rows = db.get_pending_login_requests()
    return jsonify([
        {"id": r[0], "username": r[1], "device_info": r[2], "created_at": r[3]}
        for r in rows
    ])


@app.route("/api/login-requests/<int:request_id>/approve", methods=["POST"])
@login_required
@head_admin_required
def approve_login_request_route(request_id):
    ok = db.decide_login_request(request_id, True, g.user["user_id"])
    return jsonify({"ok": ok})


@app.route("/api/login-requests/<int:request_id>/reject", methods=["POST"])
@login_required
@head_admin_required
def reject_login_request_route(request_id):
    ok = db.decide_login_request(request_id, False, g.user["user_id"])
    return jsonify({"ok": ok})


@app.route("/api/me", methods=["GET"])
@login_required
def me():
    return jsonify(g.user)


@app.route("/api/me/change-password", methods=["POST"])
@login_required
def change_password_route():
    data = request.get_json(force=True)
    old_password = data.get("old_password") or ""
    new_password = data.get("new_password") or ""
    if len(new_password) < 6:
        return jsonify({"error": "Новий пароль має містити щонайменше 6 символів"}), 400
    current_hash = db.get_password_hash(g.user["user_id"])
    if current_hash is None or not db.verify_password(old_password, current_hash):
        return jsonify({"error": "Поточний пароль невірний"}), 400
    db.change_password(g.user["user_id"], new_password)
    return jsonify({"ok": True})


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
@login_required
def serve_photo(filename):
    return send_from_directory(UPLOAD_DIR, filename)



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
    admins = db.list_users(role="admin")
    parts_admins = db.list_users(role="parts_admin")
    return jsonify(
        [{"id": r[0], "username": r[1], "role": "admin"} for r in admins] +
        [{"id": r[0], "username": r[1], "role": "parts_admin"} for r in parts_admins]
    )


@app.route("/api/admins", methods=["POST"])
@login_required
@head_admin_required
def add_admin_route():
    data = request.get_json(force=True)
    role = data.get("role", "admin")
    if role not in ("admin", "parts_admin"):
        return jsonify({"error": "Невірна роль"}), 400
    ok = db.add_user(data["username"], data["password"], role, None)
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
    users_count, repairs_count = db.rename_location(data["old_location"], data["new_location"])
    return jsonify({"users_updated": users_count, "repairs_updated": repairs_count})


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
    result = []
    for r in rows:
        parts = db.get_part_requests_for_repair(r[0])
        result.append({
            "id": r[0], "photo_url": photo_url(r[1]), "receipt_number": r[2], "intake_date": r[3],
            "parts": [{"link": p[0], "note": p[1], "status": p[2]} for p in parts],
        })
    return jsonify(result)


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
@parts_or_admin_required
def pending_repair_delete_requests_route():
    location = request.args.get("location")
    rows = db.get_pending_repair_delete_requests(location)
    return jsonify([
        {"id": r[0], "repair_id": r[1], "location": r[2], "receipt_number": r[3], "requested_at": r[4]}
        for r in rows
    ])


@app.route("/api/repair-delete-requests/<int:request_id>/approve", methods=["POST"])
@login_required
@parts_or_admin_required
def approve_repair_delete_request_route(request_id):
    ok = db.decide_repair_delete_request(request_id, True, g.user["user_id"])
    return jsonify({"ok": ok})


@app.route("/api/repair-delete-requests/<int:request_id>/reject", methods=["POST"])
@login_required
@parts_or_admin_required
def reject_repair_delete_request_route(request_id):
    ok = db.decide_repair_delete_request(request_id, False, g.user["user_id"])
    return jsonify({"ok": ok})


@app.route("/api/part-requests", methods=["POST"])
@login_required
def create_part_request_route():
    location = seller_location(g.user)
    if not location:
        return jsonify({"error": "Ця дія доступна лише продавцю"}), 403
    data = request.get_json(force=True)
    link = (data.get("link") or "").strip()
    if not link:
        return jsonify({"error": "Вставте посилання на запчастину"}), 400
    note = (data.get("note") or "").strip()
    repair_id = data.get("repair_id") or None
    request_id = db.create_part_request(location, link, note, g.user["user_id"], repair_id)
    notify_admins_push(
        "🔩 Запит на запчастину",
        f"{g.user['username']} ({location}): {note or link}",
    )
    return jsonify({"ok": True, "request_id": request_id})


@app.route("/api/part-requests/pending", methods=["GET"])
@login_required
@parts_or_admin_required
def pending_part_requests_route():
    db.cleanup_old_part_requests()
    location = request.args.get("location")
    rows = db.get_pending_part_requests(location)
    return jsonify([
        {"id": r[0], "location": r[1], "link": r[2], "note": r[3], "requested_at": r[6],
         "repair_id": r[7], "receipt_number": r[8]}
        for r in rows
    ])


@app.route("/api/part-requests/<int:request_id>/done", methods=["POST"])
@login_required
@parts_or_admin_required
def mark_part_request_done_route(request_id):
    ok = db.mark_part_request_done(request_id, g.user["user_id"])
    return jsonify({"ok": ok})


@app.route("/api/part-requests/all", methods=["GET"])
@login_required
@parts_or_admin_required
def all_part_requests_route():
    """Уся історія (будь-який статус) на всіх точках - доступно й адміну
    запчастин (лише перегляд), і повному/головному адміну (перегляд +
    редагування/видалення - обмежується окремо на рівні PATCH/DELETE)."""
    db.cleanup_old_part_requests()
    location = request.args.get("location")
    rows = db.get_all_part_requests(location)
    return jsonify([
        {"id": r[0], "location": r[1], "link": r[2], "note": r[3], "requested_by": r[4],
         "status": r[5], "requested_at": r[6], "repair_id": r[7], "receipt_number": r[8]}
        for r in rows
    ])


@app.route("/api/part-requests/mine", methods=["GET"])
@login_required
def my_part_requests_route():
    """Власна історія запитів продавця - щоб бачити й видаляти лише свої."""
    rows = db.get_part_requests_by_user(g.user["user_id"])
    return jsonify([
        {"id": r[0], "location": r[1], "link": r[2], "note": r[3], "status": r[5],
         "requested_at": r[6], "repair_id": r[7], "receipt_number": r[8]}
        for r in rows
    ])


@app.route("/api/part-requests/unlinked", methods=["GET"])
@login_required
def unlinked_part_requests_route():
    """Власні запити продавця без прив'язки до квитанції - щоб можна було
    прив'язати їх, коли квитанція буде оформлена з клієнтом."""
    rows = db.get_unlinked_part_requests_by_user(g.user["user_id"])
    return jsonify([
        {"id": r[0], "link": r[1], "note": r[2], "requested_at": r[3]}
        for r in rows
    ])


@app.route("/api/part-requests/<int:request_id>/link-repair", methods=["POST"])
@login_required
def link_part_request_route(request_id):
    """Продавець прив'язує СВІЙ раніше надісланий запит до квитанції на
    СВОЇЙ точці."""
    row = db.get_part_request_by_id(request_id)
    if row is None:
        return jsonify({"error": "Запит не знайдено"}), 404
    _, location, link, note, requested_by, status, repair_id = row
    if requested_by != g.user["user_id"] and g.user["role"] not in ("admin", "head_admin"):
        return jsonify({"error": "Це не ваш запит"}), 403

    data = request.get_json(force=True)
    new_repair_id = data.get("repair_id")
    if new_repair_id:
        repair = db.get_repair_by_id(new_repair_id)
        if repair is None or repair[4] != location:
            return jsonify({"error": "Квитанцію не знайдено на цій точці"}), 404

    db.link_part_request_to_repair(request_id, new_repair_id or None)
    return jsonify({"ok": True})


@app.route("/api/part-requests/<int:request_id>", methods=["PATCH"])
@login_required
@admin_required
def update_part_request_route(request_id):
    """Редагування посилання/коментаря/прив'язки до квитанції - лише повний/головний адмін."""
    data = request.get_json(force=True)
    link = (data.get("link") or "").strip()
    if not link:
        return jsonify({"error": "Посилання не може бути порожнім"}), 400
    note = (data.get("note") or "").strip()
    repair_id = data.get("repair_id") or None
    ok = db.update_part_request(request_id, link, note, repair_id)
    return jsonify({"ok": ok})


@app.route("/api/part-requests/<int:request_id>", methods=["DELETE"])
@login_required
def delete_part_request_route(request_id):
    """Видалення: адмін/головний адмін - будь-яке; продавець - лише своє;
    адмін запчастин (parts_admin) видаляти не може взагалі."""
    row = db.get_part_request_by_id(request_id)
    if row is None:
        return jsonify({"error": "Запит не знайдено"}), 404
    _, location, link, note, requested_by, status, repair_id = row

    if g.user["role"] in ("admin", "head_admin"):
        db.delete_part_request(request_id)
        return jsonify({"ok": True})

    if g.user["role"] == "seller" and requested_by == g.user["user_id"]:
        db.delete_part_request(request_id)
        return jsonify({"ok": True})

    return jsonify({"error": "Немає прав на видалення цього запиту"}), 403


@app.route("/api/settings/part-retention", methods=["GET"])
@login_required
@head_admin_required
def get_part_retention_route():
    days = db.get_setting(db.PART_RETENTION_SETTING_KEY)
    return jsonify({"days": int(days) if days else None})


@app.route("/api/settings/part-retention", methods=["POST"])
@login_required
@head_admin_required
def set_part_retention_route():
    data = request.get_json(force=True)
    days = data.get("days")
    if days in (None, "", 0):
        db.set_setting(db.PART_RETENTION_SETTING_KEY, "")
        return jsonify({"ok": True, "days": None})
    try:
        days = int(days)
        if days <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Кількість днів має бути додатним числом"}), 400
    db.set_setting(db.PART_RETENTION_SETTING_KEY, days)
    return jsonify({"ok": True, "days": days})


@app.route("/api/repairs/report", methods=["GET"])
@login_required
def repairs_report_route():
    location = request.args.get("location") or seller_location(g.user)
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_repairs_by_period(location, start, end)
    result = []
    for r in rows:
        parts = db.get_part_requests_for_repair(r[0])
        result.append({
            "id": r[0], "photo_url": photo_url(r[1]), "receipt_number": r[2],
            "intake_date": r[3], "completion_date": r[4], "cost": r[5], "payment_method": r[6],
            "parts": [{"link": p[0], "note": p[1], "status": p[2]} for p in parts],
        })
    return jsonify(result)


@app.route("/api/repairs/report.xlsx", methods=["GET"])
@login_required
def repairs_report_xlsx():
    location = request.args.get("location") or seller_location(g.user)
    start = request.args.get("start")
    end = request.args.get("end")
    rows = db.get_repairs_by_period(location, start, end)
    parts_by_repair = {r[0]: db.get_part_requests_for_repair(r[0]) for r in rows}
    buf = reports.build_repairs_excel(rows, parts_by_repair)
    filename = f"ремонти_{location}_{start}_{end}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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
