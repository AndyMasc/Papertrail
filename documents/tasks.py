import logging
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django_qstash import stashed_task
from records.models import Record

from .models import DocumentData, DocumentStatus
from .ocr_helpers import prepare_image_for_gemini
from .storage import s3, BUCKET

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT = 60
OCR_CACHE_TTL = 900
MAX_OCR_RETRIES = 3

try:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel, Field

    from records.models import Record

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    class OCRResult(BaseModel):
        title: str = Field(
            description="2-5 word title: 'Merchant + Item'. Shorten product names; preserve identity. Default to shortest identifier for warranties/loans. No invoice numbers/dates. If no clear title exists, use a generic description like 'Financial Document', 'Receipt', or 'Untitled'"
        )
        merchant: str | None = Field(
            default=None,
            description="The business name. Infer from logos if clear. Default to null if ambiguous.",
        )
        balance: float | None = Field(
            default=None,
            description="Total monetary sum as a raw number. No symbols, commas or currency symbols.",
        )
        products: list[str] = Field(
            default_factory=list,
            description="List of items. Standardize typos, expand abbreviations, use Title Case. If no products are listed, use an empty list.",
        )
        transaction_date: str | None = Field(
            default=None, description="Date in YYYY-MM-DD format."
        )
        expiry_date: str | None = Field(
            default=None, description="Date in YYYY-MM-DD format."
        )
        record_type: Record.RecordTypes = Field(
            description="Strictly classify the document type. If no clear type can be found, default to EXPENSE_RECEIPT"
        )

    CONFIG = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=OCRResult,
        system_instruction="Extract data exactly as it appears from the image. Never hallucinate or invent information. No preamble.",
        temperature=0.0,
        max_output_tokens=400,
    )
except ImportError:
    client = None
    OCRResult = None
    CONFIG = None


class GeminiOCRError(Exception):
    """Raised when Gemini OCR fails after all retries."""


def _get_cache_key(document_id: int) -> str:
    return f"ocr_status_{document_id}"


def _set_document_status(document_id: int, status: str) -> None:
    DocumentData.objects.filter(id=document_id).update(status=status)


def _get_document_status(document_id: int) -> str | None:
    return (
        DocumentData.objects.filter(id=document_id)
        .values_list("status", flat=True)
        .first()
    )


def _increment_ocr_retries(document_id: int) -> int:
    doc = DocumentData.objects.filter(id=document_id).first()
    if doc:
        doc.ocr_retries += 1
        doc.save(update_fields=["ocr_retries"])
        return doc.ocr_retries
    return 0


def _fetch_from_r2(filepath: str) -> bytes:
    response = s3.get_object(Bucket=BUCKET, Key=filepath)
    body = response["Body"]
    if hasattr(body, "read"):
        return body.read()
    return b"".join(chunk for chunk in body.iter_chunks(chunk_size=1024 * 1024))


def _process_image(image_bytes: bytes, filepath: str) -> types.Part:
    if filepath.lower().endswith(".pdf"):
        return types.Part.from_bytes(data=image_bytes, mime_type="application/pdf")

    image_bytes = prepare_image_for_gemini(image_bytes)
    return types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")


def _call_gemini(part: types.Part) -> dict[str, Any]:
    result = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=[part],
        config=CONFIG,
    )
    if result.parsed is not None:
        return result.parsed.model_dump(mode="json")

    raw_text = result.text.strip()
    import re

    clean_json = re.sub(r"^```json\s*|```$", "", raw_text, flags=re.MULTILINE).strip()
    return OCRResult.model_validate_json(clean_json).model_dump(mode="json")


@stashed_task(retries=MAX_OCR_RETRIES, backoff_factor=2)
def extract_document(document_id: int) -> dict[str, Any]:
    cache_key = _get_cache_key(document_id)

    current_status = _get_document_status(document_id)
    if current_status == DocumentStatus.COMPLETED:
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and "error" not in cached:
            return cached

    try:
        document = DocumentData.objects.get(id=document_id)
    except DocumentData.DoesNotExist:
        logger.error("Document %s does not exist.", document_id)
        return {"error": "Document not found."}

    _set_document_status(document_id, DocumentStatus.PROCESSING)

    try:
        image_content = _fetch_from_r2(document.filepath)
        part = _process_image(image_content, document.filepath)
        final_data = _call_gemini(part)

        cache.set(cache_key, final_data, timeout=OCR_CACHE_TTL)
        _set_document_status(document_id, DocumentStatus.COMPLETED)
        DocumentData.objects.filter(id=document_id).update(
            ocr_error="",
            did_ocr=True,
        )
        return final_data

    except Exception as exc:
        logger.warning(
            "OCR attempt failed for doc %s: %s", document_id, exc, exc_info=True
        )

        retries = _increment_ocr_retries(document_id)
        if retries >= MAX_OCR_RETRIES:
            error_payload = {
                "error": "Failed to automatically extract document details."
            }
            cache.set(cache_key, error_payload, timeout=OCR_CACHE_TTL)
            _set_document_status(document_id, DocumentStatus.ERROR)
            DocumentData.objects.filter(id=document_id).update(
                ocr_error=str(exc),
            )
            raise GeminiOCRError(f"OCR failed for document {document_id}") from exc

        raise


@stashed_task
def delete_document(filepath: str) -> None:
    if filepath:
        try:
            s3.delete_object(Bucket=BUCKET, Key=filepath)
        except Exception as e:
            logger.error("Failed to delete R2 object %s: %s", filepath, e)


# Scheduled tasks
#
@stashed_task
def delete_orphaned_documents() -> None:
    grace_period = timezone.now() - timedelta(days=1)
    orphaned_files = DocumentData.objects.filter(
        associated_record=None,
        date_added__lt=grace_period,
    ).exclude(status=DocumentStatus.DELETING)

    if not orphaned_files.exists():
        return

    file_data = list(orphaned_files.values_list("id", "filepath"))
    CHUNK_SIZE = 1000

    for i in range(0, len(file_data), CHUNK_SIZE):
        chunk = file_data[i : i + CHUNK_SIZE]
        chunk_ids = [item[0] for item in chunk]
        chunk_paths = [item[1] for item in chunk if item[1]]

        if not chunk_paths:
            continue

        try:
            s3.delete_objects(
                Bucket=BUCKET,
                Delete={
                    "Objects": [
                        {"Key": filepath.lstrip("/")} for filepath in chunk_paths
                    ]
                },
            )
            DocumentData.objects.filter(id__in=chunk_ids).delete()
        except Exception as e:
            logger.error(
                "Failed to delete bulk chunk of orphaned documents: %s",
                e,
                exc_info=True,
            )
            continue

    logger.info("Orphaned documents cleanup completed.")


@stashed_task
def reconcile_documents() -> None:
    stale_cutoff = timezone.now() - timedelta(minutes=30)

    abandoned_uploads = DocumentData.objects.filter(
        filepath__isnull=False,
        status=DocumentStatus.PENDING_UPLOAD,
        date_added__lt=stale_cutoff,
    )

    deleted_count = 0
    for doc in abandoned_uploads.iterator(chunk_size=200):
        if not doc.filepath:
            continue
        try:
            s3.delete_object(Bucket=BUCKET, Key=doc.filepath)
            doc.delete()
            deleted_count += 1
        except Exception as e:
            logger.error("Failed to cleanup abandoned upload %s: %s", doc.id, e)
            continue

    if deleted_count:
        logger.info(
            "Reconciliation: cleaned up %d stale pending uploads.",
            deleted_count,
        )

    dangling_r2_keys = DocumentData.objects.filter(
        status=DocumentStatus.ERROR,
        date_added__lt=timezone.now() - timedelta(days=2),
    )
    dangling_count = dangling_r2_keys.count()
    if dangling_count:
        dangling_r2_keys.delete()
        logger.info(
            "Reconciliation: removed %d dangling error records.", dangling_count
        )
