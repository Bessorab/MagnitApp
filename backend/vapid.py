"""
Генерація та зберігання VAPID-ключів для Web Push (сповіщення в браузері,
аналог push-повідомлень Telegram - але вже незалежно від Telegram).

VAPID = стандартний спосіб браузерів довіряти серверу, який надсилає push.
Використовуємо бібліотеку cryptography напряму (без py_vapid), оскільки
потрібна лише крива P-256, яку cryptography повністю підтримує.

ВАЖЛИВО: на хостингах на кшталт Render безкоштовного тарифу файлова система
скидається при кожному перезапуску сервера. Якщо зберігати ключі лише у
файлі - при кожному перезапуску генеруються НОВІ ключі, і всі раніше
підписані браузери (продавці/адміни, які натиснули "Увімкнути сповіщення")
миттєво й непомітно перестають отримувати сповіщення, бо їхня підписка
прив'язана до вже неіснуючого старого ключа.

Тому тут спочатку перевіряються змінні середовища VAPID_PRIVATE_KEY і
VAPID_PUBLIC_KEY (стабільні між перезапусками, як і JWT_SECRET) - і лише
якщо їх немає, використовується файл (зручно для локальної розробки, але
для Render їх обов'язково варто задати - див. generate_env_keys() нижче).
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


def _b64url_decode(s: str) -> bytes:
    padding = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _keypair_to_pem_and_public(private_key) -> tuple:
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_numbers = private_key.public_key().public_numbers()
    x = public_numbers.x.to_bytes(32, "big")
    y = public_numbers.y.to_bytes(32, "big")
    public_b64 = _b64url(b"\x04" + x + y)
    return private_pem, public_b64


def generate_and_save_keys():
    os.makedirs(KEYS_DIR, exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem, public_b64 = _keypair_to_pem_and_public(private_key)
    with open(PRIVATE_KEY_PATH, "wb") as f:
        f.write(private_pem)
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


def generate_env_keys():
    """Генерує НОВУ пару ключів у вигляді, готовому для копіювання у змінні
    середовища (однорядкові значення - на відміну від PEM, який
    багаторядковий і незручний для полів середовища)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_value = private_key.private_numbers().private_value
    private_b64 = _b64url(private_value.to_bytes(32, "big"))
    _, public_b64 = _keypair_to_pem_and_public(private_key)
    return private_b64, public_b64


def _private_key_from_env_scalar(private_b64: str):
    private_value = int.from_bytes(_b64url_decode(private_b64), "big")
    return ec.derive_private_key(private_value, ec.SECP256R1())


def get_private_key_pem() -> str:
    env_scalar = os.environ.get("VAPID_PRIVATE_KEY")
    if env_scalar:
        private_key = _private_key_from_env_scalar(env_scalar)
        private_pem, _ = _keypair_to_pem_and_public(private_key)
        return private_pem.decode()
    private_pem, _ = load_or_create_keys()
    return private_pem.decode()


def get_public_key_b64() -> str:
    env_public = os.environ.get("VAPID_PUBLIC_KEY")
    if env_public:
        return env_public
    _, public_b64 = load_or_create_keys()
    return public_b64


def using_env_keys() -> bool:
    return bool(os.environ.get("VAPID_PRIVATE_KEY") and os.environ.get("VAPID_PUBLIC_KEY"))


if __name__ == "__main__":
    priv, pub = generate_env_keys()
    print("Скопіюйте ці два рядки у змінні середовища Render (Environment):\n")
    print(f"VAPID_PRIVATE_KEY={priv}")
    print(f"VAPID_PUBLIC_KEY={pub}")

