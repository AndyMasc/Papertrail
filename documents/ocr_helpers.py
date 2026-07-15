import io
import logging
import cv2
from deskew import determine_skew
import numpy as np

from PIL import Image, ImageFilter

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


# Deskew, recolor, compress and save as webp to reduce gemini latency
def prepare_image_for_gemini(image_bytes: bytes) -> bytes:
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Could not decode image bytes")

        small_grayscale = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (500, 500))
        angle = determine_skew(small_grayscale)

        if angle:
            (h, w) = img.shape[:2]
            center = (w // 2, h // 2)
            matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            img = cv2.warpAffine(
                img,
                matrix,
                (w, h),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_REPLICATE,
            )

        max_dim = 1200
        h, w = img.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = cv2.resize(
                img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
            )

        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb).filter(ImageFilter.SHARPEN)

        buf = io.BytesIO()
        pil_img.save(buf, format="WEBP", quality=85, optimize=True)
        return buf.getvalue()

    except Exception as e:
        logger.error("Failed to optimize image: %s", e)
        return image_bytes
