import logging
import mimetypes
import re
from datetime import timedelta
from typing import List, Optional

from django.conf import settings
from django.core.cache import cache
from django.db.models.signals import post_delete
from django.utils import timezone
from django_qstash import stashed_task
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from records.models import Record

from .models import DocumentData, DocumentStatus
from .ocr_helpers import prepare_image_for_gemini
from .signals import post_delete_document
from .storage import s3

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT = 60

client = genai.Client(api_key=settings.GEMINI_API_KEY)


class OCRResult(BaseModel):
    title: str = Field(
        description="2-5 word title: 'Merchant + Item'. Shorten product names; preserve identity. Default to shortest identifier for warranties/loans. No invoice numbers/dates. If no clear title exists, use a generic description like 'Financial Document', 'Receipt', or 'Untitled'"
    )
    merchant: Optional[str] = Field(
        default=None,
        description="The business name. Infer from logos if clear. Default to null if ambiguous.",
    )
    balance: Optional[float] = Field(
        default=None,
        description="Total monetary sum as a raw number. No symbols, commas or currency symbols.",
    )
    products: List[str] = Field(
        description="List of items. Standardize typos, expand abbreviations, use Title Case. If no products are listed, use an empty list."
    )
    transaction_date: Optional[str] = Field(
        default=None, description="Date in YYYY-MM-DD format."
    )
    expiry_date: Optional[str] = Field(
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


class GeminiOCRError(Exception):
    """Raised when Gemini OCR fails after all retries."""


# Helper functions for background tasks
#
def _set_document_status(document_id: int, status: str) -> None:
    DocumentData.objects.filter(id=document_id).update(status=status)


def _get_document_status(document_id: int) -> str | None:
    return (
        DocumentData.objects.filter(id=document_id)
        .values_list("status", flat=True)
        .first()
    )


def _fetch_from_r2(filepath: str) -> bytes:
    response = s3.get_object(
        Bucket=settings.R2_STORAGE_BUCKET_NAME,
        Key=filepath,
    )
    return response["Body"].read()


def _process_image(image_bytes: bytes, filepath: str) -> types.Part:
    mime_type = mimetypes.guess_type(filepath)[0] or "image/jpeg"
    if mime_type == "application/pdf":
        return types.Part.from_bytes(data=image_bytes, mime_type="application/pdf")

    image_bytes = prepare_image_for_gemini(image_bytes)
    return types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")


def _call_gemini(part: types.Part) -> dict:
    result = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=[part],
        config=CONFIG,
    )
    if result.parsed is not None:
        return result.parsed.model_dump(mode="json")

    raw_text = result.text.strip()
    clean_json = re.sub(r"^```json\s*|```$", "", raw_text, flags=re.MULTILINE).strip()
    return OCRResult.model_validate_json(clean_json).model_dump(mode="json")


# Background tasks
#
@stashed_task(retries=3, backoff_factor=2)
def extract_document(document_id: int) -> dict:
    cache_key = f"ocr_status_{document_id}"

    if False:
        import time

        time.sleep(4)
        mock_data = OCRResult(
            title="Mock Title",
            merchant="Mock Merchant",
            balance=125.50,
            products=["Mock product 1", "Mock product 2", "Mock product 3"],
            transaction_date="2026-01-01",
            expiry_date="2026-01-09",
            record_type=Record.RecordTypes.EXPENSE_RECEIPT,
        ).model_dump(mode="json")
        cache.set(cache_key, mock_data, timeout=900)
        _set_document_status(document_id, DocumentStatus.COMPLETED)
        return mock_data

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

        cache.set(cache_key, final_data, timeout=900)
        _set_document_status(document_id, DocumentStatus.COMPLETED)
        return final_data

    except Exception as exc:
        logger.warning(
            "OCR attempt framework execution failed for doc %s: %s", document_id, exc
        )

        # When max retries are exceeded by QStash runner infrastructure:
        error_payload = {"error": "Failed to automatically extract document details."}
        cache.set(cache_key, error_payload, timeout=900)
        _set_document_status(document_id, DocumentStatus.ERROR)
        raise GeminiOCRError(f"OCR failed for document {document_id}") from exc


@stashed_task
def delete_document(filepath: str) -> None:
    if filepath:
        s3.delete_object(Bucket=settings.R2_STORAGE_BUCKET_NAME, Key=filepath)


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

        deletion_payload = {
            "Objects": [{"Key": filepath.lstrip("/")} for filepath in chunk_paths]
        }
        try:
            s3.delete_objects(
                Bucket=settings.R2_STORAGE_BUCKET_NAME, Delete=deletion_payload
            )
            post_delete.disconnect(post_delete_document, sender=DocumentData)
            DocumentData.objects.filter(id__in=chunk_ids).delete()
        except Exception as e:
            logger.error(
                "Failed to delete bulk chunk of orphaned documents: %s",
                e,
                exc_info=True,
            )
            continue
        finally:
            post_delete.connect(post_delete_document, sender=DocumentData)


@stashed_task
def reconcile_documents() -> None:
    stale_cutoff = timezone.now() - timedelta(minutes=30)

    # Only destroy PENDING_UPLOAD rows here.
    # Valid uploaded rows without forms finalized must fallback strictly into delete_orphaned_documents window (1 Day Grace Period)
    abandoned_uploads = DocumentData.objects.filter(
        filepath__isnull=False,
        status=DocumentStatus.PENDING_UPLOAD,
        date_added__lt=stale_cutoff,
    )

    deleted_count = 0
    for doc in abandoned_uploads.iterator(chunk_size=200):
        if not doc.filepath:
            continue
        s3.delete_object(Bucket=settings.R2_STORAGE_BUCKET_NAME, Key=doc.filepath)
        doc.delete()
        deleted_count += 1

    if deleted_count:
        logger.info(
            "Reconciliation: cleaned up %d scale dynamic pending uploads.",
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
