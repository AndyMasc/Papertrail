import json
import logging

import cv2
import numpy as np
from PIL import Image
from pillow_heif import is_supported, read_heif

from deskew import determine_skew

logger = logging.getLogger(__name__)

MAX_DIMENSION = 1200
WEBP_QUALITY = 85
SKEW_MAX_DIM = 500
SKEW_THRESHOLD = 0.5


def ocr_data_to_form_initial(data: dict | None) -> dict:
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


def _decode_image(image_bytes: bytes) -> np.ndarray | None:
    if is_supported(image_bytes):
        try:
            heif_file = read_heif(image_bytes)
            img_rgb = np.frombuffer(heif_file.data, dtype=np.uint8).reshape(
                heif_file.size[1], heif_file.size[0], 3
            )
            return cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
        except Exception as e:
            logger.error("Failed to decode HEIC bytes: %s", e)
            return None

    nparr = np.frombuffer(image_bytes, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def _deskew_image(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    scale = SKEW_MAX_DIM / max(h, w)
    if scale < 1.0:
        small_gray = cv2.resize(
            cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
            (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    else:
        small_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    angle = determine_skew(small_gray)

    if angle and abs(angle) > SKEW_THRESHOLD:
        center = (w // 2, h // 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        img = cv2.warpAffine(
            img,
            matrix,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
    return img


def _resize_image(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    if max(h, w) > MAX_DIMENSION:
        scale = MAX_DIMENSION / max(h, w)
        img = cv2.resize(
            img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
        )
    return img


def _encode_webp(img: np.ndarray) -> bytes:
    encode_param = [int(cv2.IMWRITE_WEBP_QUALITY), WEBP_QUALITY]
    success, encoded_img = cv2.imencode(".webp", img, encode_param)
    if not success:
        raise ValueError("Failed to encode image to WebP")
    return encoded_img.tobytes()


def prepare_image_for_gemini(image_bytes: bytes) -> bytes:
    try:
        img = _decode_image(image_bytes)
        if img is None:
            raise ValueError("Could not decode image bytes")

        img = _deskew_image(img)
        img = _resize_image(img)
        return _encode_webp(img)

    except Exception as e:
        logger.error("Failed to optimize image: %s", e)
        return image_bytes


def prepare_image_from_pil(image: Image.Image) -> bytes:
    img_array = np.array(image)
    if img_array.ndim == 3 and img_array.shape[2] == 3:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    elif img_array.ndim == 3 and img_array.shape[2] == 4:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2BGR)
    else:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_GRAY2BGR)

    img_array = _deskew_image(img_array)
    img_array = _resize_image(img_array)
    return _encode_webp(img_array)
