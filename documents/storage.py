import logging
import uuid

import boto3
import filetype
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings

try:
    import magic as python_magic

    HAS_MAGIC = True
except (ImportError, OSError):
    python_magic = None
    HAS_MAGIC = False

logger = logging.getLogger(__name__)

BUCKET = settings.R2_STORAGE_BUCKET_NAME
TIMEOUT_SECONDS = 30

ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    }
)

s3 = boto3.client(
    service_name="s3",
    endpoint_url=settings.R2_S3_ENDPOINT_URL,
    aws_access_key_id=settings.R2_ACCESS_KEY_ID,
    aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
    region_name="auto",
    config=Config(
        signature_version="s3v4",
        connect_timeout=TIMEOUT_SECONDS,
        read_timeout=TIMEOUT_SECONDS,
    ),
)


def generate_upload_key(user_id: int, extension: str) -> str:
    safe_ext = extension.lstrip(".").lower()
    return f"users/{user_id}/{uuid.uuid4()}.{safe_ext}"


def generate_presigned_post(user_id: int, key: str, content_type: str) -> str:
    return s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=900,
    )


def generate_read_presigned_url(key: str) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": BUCKET,
            "Key": key,
        },
        ExpiresIn=900,
    )


def verify_r2_object_exists(key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError:
        return False


def get_r2_object_head(key: str) -> dict | None:
    try:
        return s3.head_object(Bucket=BUCKET, Key=key)
    except ClientError:
        return None


def gatekeeper_validate_r2_object(key: str) -> dict:
    head = get_r2_object_head(key)
    if head is None:
        return {"valid": False, "error": "Object not found in R2."}

    r2_content_type = head.get("ContentType", "")
    content_length = head.get("ContentLength", 0)

    if content_length > 50 * 1024 * 1024:
        s3.delete_object(Bucket=BUCKET, Key=key)
        return {"valid": False, "error": "File exceeds 50MB limit."}

    if content_length == 0:
        s3.delete_object(Bucket=BUCKET, Key=key)
        return {"valid": False, "error": "Empty file rejected."}

    r2_ct_normalized = r2_content_type.lower().split(";")[0].strip()
    if r2_ct_normalized not in ALLOWED_MIME_TYPES:
        s3.delete_object(Bucket=BUCKET, Key=key)
        return {
            "valid": False,
            "error": f"Rejected MIME type: {r2_ct_normalized}",
        }

    try:
        resp = s3.get_object(
            Bucket=BUCKET,
            Key=key,
            Range="bytes=0-2047",
        )
        header_bytes = resp["Body"].read()

        if HAS_MAGIC:
            detected_mime = python_magic.from_buffer(header_bytes, mime=True)
        else:
            kind = filetype.guess(header_bytes)
            detected_mime = kind.mime if kind else r2_ct_normalized

        if detected_mime not in ALLOWED_MIME_TYPES:
            s3.delete_object(Bucket=BUCKET, Key=key)
            return {
                "valid": False,
                "error": f"File signature mismatch: {detected_mime}",
            }
    except ClientError as e:
        logger.warning("Gatekeeper partial read failed for %s: %s", key, e)

    return {"valid": True, "content_type": r2_ct_normalized}


def delete_r2_object(key: str) -> None:
    try:
        s3.delete_object(Bucket=BUCKET, Key=key)
    except ClientError as e:
        logger.error("Failed to delete R2 object %s: %s", key, e)


def delete_r2_objects_batch(keys: list[str]) -> None:
    if not keys:
        return
    CHUNK = 1000
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i : i + CHUNK]
        try:
            s3.delete_objects(
                Bucket=BUCKET,
                Delete={"Objects": [{"Key": k} for k in chunk]},
            )
        except ClientError as e:
            logger.error("Batch R2 deletion failed for chunk: %s", e)
