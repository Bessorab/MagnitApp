"""
Розпізнавання штрихкоду та порівняння фото за схожістю - той самий підхід,
що вже перевірений і працює у Telegram-версії бота.
"""
import io
from PIL import Image, ImageOps
import imagehash

try:
    from pyzbar.pyzbar import decode
    BARCODE_AVAILABLE = True
except ImportError:
    # На деяких безкоштовних хостингах немає системної бібліотеки libzbar0,
    # і встановити її неможливо. Без неї застосунок все одно працює - просто
    # штрихкоди не розпізнаються, лишається пошук за схожістю фото.
    BARCODE_AVAILABLE = False

    def decode(img):
        return []

MATCH_THRESHOLD = 20
MAX_MATCH_RESULTS = 3


def compute_image_hash(image_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(image_bytes))
    return str(imagehash.phash(image))


def detect_barcodes(image_bytes: bytes):
    """Намагається розпізнати штрихкод кількома способами обробки фото -
    оригінал, відтінки сірого, підвищений контраст, збільшений розмір."""
    img = Image.open(io.BytesIO(image_bytes))

    candidates = [img]
    try:
        gray = img.convert("L")
        candidates.append(gray)
        candidates.append(ImageOps.autocontrast(gray))
        width, height = img.size
        if max(width, height) < 1500:
            scale = 1500 / max(width, height)
            upscaled = gray.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
            candidates.append(upscaled)
    except Exception:
        pass

    for candidate in candidates:
        raw_barcodes = decode(candidate)
        if raw_barcodes:
            sorted_codes = sorted(raw_barcodes, key=lambda b: 1 if b.type == "QRCODE" else 0)
            return sorted_codes[0].data.decode("utf-8")

    return None


def find_close_matches(items_with_hashes, target_hash_str):
    """items_with_hashes: список кортежів (id, photo_path, name, color, price, quantity, photo_hash)."""
    target_hash = imagehash.hex_to_hash(target_hash_str)
    scored = []
    for item_id, photo_path, name, color, price, quantity, photo_hash in items_with_hashes:
        if not photo_hash:
            continue
        try:
            existing_hash = imagehash.hex_to_hash(photo_hash)
        except (ValueError, TypeError):
            continue
        distance = target_hash - existing_hash
        if distance <= MATCH_THRESHOLD:
            scored.append((distance, item_id, photo_path, name, color, price, quantity))
    scored.sort(key=lambda row: row[0])
    return [
        (item_id, photo_path, name, color, price, quantity)
        for _, item_id, photo_path, name, color, price, quantity in scored[:MAX_MATCH_RESULTS]
    ]
