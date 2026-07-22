"""
Генерація та зберігання VAPID-ключів для Web Push (сповіщення в браузері,
аналог push-повідомлень Telegram - але вже незалежно від Telegram).

VAPID = стандартний спосіб браузерів довіряти серверу, який надсилає push.
Використовуємо бібліотеку cryptography напряму (без py_vapid), оскільки
потрібна лише крива P-256, яку cryptography повністю підтримує.
"""
import os
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

KEYS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vapid_keys")
PRIVATE_KEY_PATH = os.path.join(KEYS_DIR, "private_key.pem")
PUBLIC_KEY_PATH = os.path.join(KEYS_DIR, "public_key.b64")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def generate_and_save_keys():
    os.makedirs(KEYS_DIR, exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(PRIVATE_KEY_PATH, "wb") as f:
        f.write(private_pem)

    public_numbers = private_key.public_key().public_numbers()
    # Незжатий формат публічного ключа (0x04 + x + y), саме такий очікує браузер
    x = public_numbers.x.to_bytes(32, "big")
    y = public_numbers.y.to_bytes(32, "big")
    raw_public = b"\x04" + x + y
    public_b64 = _b64url(raw_public)
    with open(PUBLIC_KEY_PATH, "w") as f:
        f.write(public_b64)
    return private_pem, public_b64


def load_or_create_keys():
    if not (os.path.exists(PRIVATE_KEY_PATH) and os.path.exists(PUBLIC_KEY_PATH)):
        return generate_and_save_keys()
    with open(PRIVATE_KEY_PATH, "rb") as f:
        private_pem = f.read()
    with open(PUBLIC_KEY_PATH) as f:
        public_b64 = f.read().strip()
    return private_pem, public_b64


def get_private_key_pem() -> str:
    private_pem, _ = load_or_create_keys()
    return private_pem.decode()


def get_public_key_b64() -> str:
    _, public_b64 = load_or_create_keys()
    return public_b64
