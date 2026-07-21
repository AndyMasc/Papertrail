import logging
import re
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone
from django_qstash import shared_task

from .models import DocumentData, DocumentStatus
from .ocr_helpers import prepare_image_for_gemini
from .storage import BUCKET, s3

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT = 60
OCR_CACHE_TTL = 604800
MAX_OCR_RETRIES = 3

try:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field

    from records.models import Record

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    class OCRResult(BaseModel):
        title: str = Field(
            description="2-5 word title: 'Merchant + Item'. Shorten product names; preserve identity. Default to shortest identifier for warranties/loans. No invoice numbers/dates. If no clear title exists, use a generic description like 'Financial Document', 'Receipt', or 'Untitled'. Absolute Max length 255 chars."
        )
        merchant: str | None = Field(
            default=None,
            description="The business name. Infer from logos if clear. Default to null if ambiguous. Absolute max length 255 chars.",
        )
        balance: float | None = Field(
            default=None,
            description="Total monetary sum as a raw number. No symbols, commas or currency symbols.",
        )
        products: list[str] = Field(
            default_factory=list,
            description="List of items. Standardize typos, expand abbreviations, use Title Case. If no products are listed, use an empty list.",
        )
        transaction_date: str | None = Field(default=None, description="Date in YYYY-MM-DD format.")
        expiry_date: str | None = Field(default=None, description="Date in YYYY-MM-DD format.")
        record_type: Record.RecordTypes = Field(
            description="Strictly classify the document type. If no clear type can be found, default to EXPENSE_RECEIPT"
        )
        suggested_folder: str | None = Field(
            default=None,
            description="Choose from the user's available folders provided in the prompt. If the document clearly belongs in one, return that folder name exactly. If none fit, return null.",
        )

    CONFIG = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=OCRResult,
        system_instruction="Extract data exactly as it appears from the image. Never hallucinate or invent information. No preamble. The user's available folders are provided in the context — match them exactly if applicable.",
        temperature=0.0,
        max_output_tokens=700,
    )
except ImportError:
    client = None
    OCRResult = None
    CONFIG = None


class GeminiOCRError(Exception):
    """Raised when Gemini OCR fails after all retries."""


def _get_cache_key(document_id: int) -> str:
    return f"ocr_status_{document_id}"


def _normalize_s3_key(filepath: str) -> str:
    return filepath.lstrip("/") if filepath else ""


def _set_document_status(document_id: int, status: str, **kwargs) -> None:
    DocumentData.objects.filter(id=document_id).update(status=status, **kwargs)


def _increment_ocr_retries(document_id: int) -> int:
    DocumentData.objects.filter(id=document_id).update(ocr_retries=F("ocr_retries") + 1)
    doc = DocumentData.objects.filter(id=document_id).values("ocr_retries").first()
    return doc["ocr_retries"] if doc else 0


def _fetch_from_r2(filepath: str) -> bytes:
    key = _normalize_s3_key(filepath)
    response = s3.get_object(Bucket=BUCKET, Key=key)
    body = response["Body"]
    if hasattr(body, "read"):
        return body.read()
    return b"".join(chunk for chunk in body.iter_chunks(chunk_size=1024 * 1024))


def _process_image(image_bytes: bytes, filepath: str) -> types.Part:
    if filepath.lower().endswith(".pdf"):
        return types.Part.from_bytes(data=image_bytes, mime_type="application/pdf")

    image_bytes = prepare_image_for_gemini(image_bytes)
    return types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")


def _call_gemini(image_part: types.Part, folder_names: list[str]) -> dict[str, Any]:
    contents = []

    folder_context = (
        f"User's available folders: {', '.join(folder_names) if folder_names else 'None'}. "
        f"If the document context logically fits one of these folders, populate 'suggested_folder' with the exact name string. Otherwise null."
    )

    contents.append(folder_context)
    contents.append(image_part)

    result = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=contents,
        config=CONFIG,
    )

    if result.parsed is not None:
        return result.parsed.model_dump(mode="json")

    raw_text = result.text.strip()
    clean_json = re.sub(r"^```json\s*|```$", "", raw_text, flags=re.MULTILINE).strip()
    return OCRResult.model_validate_json(clean_json).model_dump(mode="json")


@shared_task(retries=MAX_OCR_RETRIES, backoff_factor=2)
def extract_document(document_id: int) -> dict[str, Any]:
    cache_key = _get_cache_key(document_id)

    doc_lookup = DocumentData.objects.filter(id=document_id).values("status").first()
    if not doc_lookup:
        logger.error("Document %s does not exist.", document_id)
        return {"error": "Document not found."}

    if doc_lookup["status"] == DocumentStatus.COMPLETED:
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and "error" not in cached:
            return cached

    _set_document_status(document_id, DocumentStatus.PROCESSING)

    try:
        document = (
            DocumentData.objects.select_related("user")
            .prefetch_related("user__folders")
            .get(id=document_id)
        )

        folder_names = list(document.user.folders.values_list("name", flat=True))

        image_content = _fetch_from_r2(document.filepath)
        part = _process_image(image_content, document.filepath)

        final_data = _call_gemini(part, folder_names)

        cache.set(cache_key, final_data, timeout=OCR_CACHE_TTL)
        _set_document_status(document_id, DocumentStatus.COMPLETED, ocr_error="", did_ocr=True)
        return final_data

    except Exception as exc:
        logger.warning("OCR attempt failed for doc %s: %s", document_id, exc, exc_info=True)
        retries = _increment_ocr_retries(document_id)

        if retries >= MAX_OCR_RETRIES:
            error_payload = {"error": "Failed to automatically extract document details."}
            cache.set(cache_key, error_payload, timeout=OCR_CACHE_TTL)
            _set_document_status(document_id, DocumentStatus.ERROR, ocr_error=str(exc))
            raise GeminiOCRError(f"OCR execution hard-failed for document {document_id}") from exc

        raise


@shared_task(retries=3, backoff_factor=2)
def delete_document(filepath: str) -> None:
    if filepath:
        s3.delete_object(Bucket=BUCKET, Key=_normalize_s3_key(filepath))


def _bulk_delete_documents(file_data: list[tuple[int, str]]) -> None:
    CHUNK_SIZE = 1000

    for i in range(0, len(file_data), CHUNK_SIZE):
        chunk = file_data[i : i + CHUNK_SIZE]
        chunk_ids = [item[0] for item in chunk]
        chunk_paths = [_normalize_s3_key(item[1]) for item in chunk if item[1]]

        if not chunk_ids:
            continue

        try:
            DocumentData.objects.filter(id__in=chunk_ids).delete()
        except Exception as e:
            logger.error("Failed to delete orphaned DB records: %s", e, exc_info=True)
            continue

        if not chunk_paths:
            continue

        try:
            s3.delete_objects(
                Bucket=BUCKET,
                Delete={"Objects": [{"Key": path} for path in chunk_paths]},
            )
        except Exception as e:
            logger.error(
                "R2 cleanup failed for orphaned keys (DB already cleaned): %s", e, exc_info=True
            )
            continue


@shared_task
def delete_orphaned_documents() -> None:
    grace_period = timezone.now() - timedelta(days=1)
    orphaned_files = DocumentData.objects.filter(
        associated_record=None,
        deleted_at__isnull=True,
        date_added__lt=grace_period,
        did_ocr=False,
    ).exclude(status=DocumentStatus.DELETING)

    if orphaned_files.exists():
        file_data = list(orphaned_files.values_list("id", "filepath"))
        _bulk_delete_documents(file_data)
        logger.info("Orphaned documents cleanup completed.")

    ocr_grace = timezone.now() - timedelta(days=7)
    abandoned_ocr = DocumentData.objects.filter(
        associated_record=None,
        deleted_at__isnull=True,
        date_added__lt=ocr_grace,
        did_ocr=True,
        status__in=[
            DocumentStatus.UPLOADED,
            DocumentStatus.PROCESSING,
            DocumentStatus.COMPLETED,
            DocumentStatus.ERROR,
        ],
    )

    if abandoned_ocr.exists():
        file_data = list(abandoned_ocr.values_list("id", "filepath"))
        _bulk_delete_documents(file_data)
        logger.info("Abandoned OCR documents cleanup completed.")


@shared_task
def reconcile_documents() -> None:
    stale_cutoff = timezone.now() - timedelta(minutes=30)
    abandoned_uploads = DocumentData.objects.filter(
        filepath__isnull=False,
        deleted_at__isnull=True,
        status=DocumentStatus.PENDING_UPLOAD,
        date_added__lt=stale_cutoff,
    )

    deleted_ids = []
    for doc in abandoned_uploads.iterator(chunk_size=200):
        if not doc.filepath:
            continue
        try:
            s3.delete_object(Bucket=BUCKET, Key=_normalize_s3_key(doc.filepath))
            deleted_ids.append(doc.id)
        except Exception as e:
            logger.error("Failed cleanup of object storage for upload %s: %s", doc.id, e)
            continue

    if deleted_ids:
        DocumentData.objects.filter(id__in=deleted_ids).delete()
        logger.info("Reconciliation: cleaned up %d stale pending uploads.", len(deleted_ids))

    dangling_records = DocumentData.objects.filter(
        deleted_at__isnull=True,
        status=DocumentStatus.ERROR,
        date_added__lt=timezone.now() - timedelta(days=2),
    )
    dangling_ids = list(dangling_records.values_list("id", "filepath"))
    if dangling_ids:
        for doc_id, path in dangling_ids:
            if path:
                try:
                    s3.delete_object(Bucket=BUCKET, Key=_normalize_s3_key(path))
                except Exception as e:
                    logger.error(
                        "Failed to clean up R2 object for dangling error doc %s: %s", doc_id, e
                    )
        DocumentData.objects.filter(id__in=[d[0] for d in dangling_ids]).delete()
        logger.info("Reconciliation: removed %d dangling error records.", len(dangling_ids))


@shared_task
def delete_7year_deleted_documents() -> None:
    seven_years_ago = timezone.now() - timedelta(days=365 * 7)
    expired_deleted = DocumentData.objects.filter(
        deleted_at__isnull=False,
        deleted_at__lt=seven_years_ago,
        user__settings__auto_delete_deleted_documents=True,
    )
    count = expired_deleted.count()
    if not count:
        return

    file_data = list(expired_deleted.values_list("id", "filepath"))
    _bulk_delete_documents(file_data)
    logger.info("Hard-deleted %d documents soft-deleted for 7+ years.", count)
