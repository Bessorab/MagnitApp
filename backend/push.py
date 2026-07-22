"""
Надсилання Web Push сповіщень у браузер - працює навіть коли застосунок
закритий (як push-повідомлення в Telegram), але вже без Telegram.

Потребує `pip install pywebpush` на сервері (не встановлено в середовищі
розробки - логіка написана за документацією pywebpush і перевірена
структурно, але саму доставку варто перевірити на реальному сервері
з реальним браузером).
"""
import json
import logging

logger = logging.getLogger(__name__)

try:
    from pywebpush import webpush, WebPushException
    PYWEBPUSH_AVAILABLE = True
except ImportError:
    PYWEBPUSH_AVAILABLE = False

import vapid

VAPID_CLAIMS_EMAIL = "mailto:admin@example.com"


def send_push_notification(subscription_info, title, body, url=None):
    """subscription_info: {"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}
    Повертає True при успіху, False - якщо підписку варто видалити (застаріла)."""
    if not PYWEBPUSH_AVAILABLE:
        logger.warning("pywebpush не встановлено - push-сповіщення не надіслано. Виконайте: pip install pywebpush")
        return None

    payload = json.dumps({"title": title, "body": body, "url": url or "/"})
    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=vapid.get_private_key_pem(),
            vapid_claims={"sub": VAPID_CLAIMS_EMAIL},
        )
        return True
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):
            return False  # підписка застаріла - варто видалити
        logger.error(f"Помилка надсилання push: {e}")
        return None
