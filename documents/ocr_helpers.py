import cv2
import numpy as np
import logging
from pillow_heif import is_supported, read_heif
import json
from deskew import determine_skew

logger = logging.getLogger(__name__)


def ocr_data_to_form_initial(data) -> dict:
    if not isinstance(data, dict):
        return {}

    products_data = data.get("products") or []

    if isinstance(products_data, list):
        processed_products = [
            json.dumps(p) if isinstance(p, (dict, list)) else str(p).strip()
            for p in products_data
        ]
        products_value = "\n".join(processed_products).strip()
    else:
        products_value = products_data

    return {
        "title": data.get("title"),
        "products": products_value,
        "merchant": data.get("merchant"),
        "balance": data.get("balance"),
        "transaction_date": data.get("transaction_date"),
        "expiry_date": data.get("expiry_date"),
        "record_type": data.get("record_type"),
    }


# Deskew, recolor, compress and save as webp to reduce gemini latency
def prepare_image_for_gemini(image_bytes: bytes) -> bytes:
    try:
        if is_supported(image_bytes):
            try:
                heif_file = read_heif(image_bytes)
                img_rgb = np.frombuffer(heif_file.data, dtype=np.uint8).reshape(
                    heif_file.size[1], heif_file.size[0], 3
                )
                img = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            except Exception as e:
                logger.error("Failed to decode HEIC bytes: %s", e)
                return image_bytes
        else:
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            raise ValueError("Could not decode image bytes")

        h, w = img.shape[:2]

        skew_max_dim = 500
        scale_skew = skew_max_dim / max(h, w)
        if scale_skew < 1.0:
            small_gray = cv2.resize(
                cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
                (int(w * scale_skew), int(h * scale_skew)),
                interpolation=cv2.INTER_AREA,
            )
        else:
            small_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        angle = determine_skew(small_gray)

        if angle and abs(angle) > 0.5:
            center = (w // 2, h // 2)
            matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            img = cv2.warpAffine(
                img,
                matrix,
                (w, h),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_REPLICATE,
            )
            h, w = img.shape[:2]

        max_dim = 1200
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = cv2.resize(
                img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
            )

        encode_param = [int(cv2.IMWRITE_WEBP_QUALITY), 85]
        success, encoded_img = cv2.imencode(".webp", img, encode_param)

        if not success:
            raise ValueError("Failed to encode image to WebP")

        return encoded_img.tobytes()

    except Exception as e:
        logger.error("Failed to optimize image: %s", e)
        return image_bytes
