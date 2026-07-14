from PIL import Image
import io
import logging

logger = logging.getLogger(__name__)


def ocr_data_to_form_initial(data) -> dict:
    if not isinstance(data, dict):
        return {}

    products_data = data.get("products") or []
    return {
        "title": data.get("title"),
        "products": (
            "\n".join(products_data)
            if isinstance(products_data, list)
            else products_data
        ),
        "merchant": data.get("merchant"),
        "balance": data.get("balance"),
        "transaction_date": data.get("transaction_date"),
        "expiry_date": data.get("expiry_date"),
        "record_type": data.get("record_type"),
    }


def prepare_image_for_gemini(image_bytes: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")

            max_dim = 1200
            if max(img.size) > max_dim:
                img.thumbnail((max_dim, max_dim))

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue()
    except Exception as e:
        logger.error("Failed to optimize image: %s", e)
        return image_bytes  # Return original bytes as a fallback
