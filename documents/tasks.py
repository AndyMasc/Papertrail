import logging
import mimetypes
from datetime import timedelta
from typing import List, Optional

import requests
from django.conf import settings
from django.core.cache import cache
from django.db.models.signals import post_delete
from django.utils import timezone
from django_qstash import stashed_task
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from records.models import Record

from .models import DocumentData
from .ocr_helpers import prepare_image_for_gemini
from .signals import post_delete_document
from .storage import s3

logger = logging.getLogger(__name__)

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
        description="Strictly classify the document type. If no clear type can be found, default to EXPENSE_RECIEPT"
    )


CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=OCRResult,
    system_instruction="Extract data exactly as it appears from the image. Never hallucinate or invent information. No preamble.",
    temperature=0.0,
    max_output_tokens=400,
)


class GeminiOCRError(Exception):
    """Raised when Gemini OCR fails."""


@stashed_task
def extract_document(document_id: int) -> dict:
    document = DocumentData.objects.get(id=document_id)
    cache_key = f"ocr_status_{document_id}"

    if settings.DEBUG:
        import time

        time.sleep(4)  # Simulate slow network response
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
        return mock_data

    try:
        response = s3.get_object(
            Bucket=settings.R2_STORAGE_BUCKET_NAME, Key=document.filepath
        )
        image_content = response["Body"].read()

        mime_type = mimetypes.guess_type(document.filepath)[0] or "image/jpeg"
        if mime_type == "application/pdf":
            part = types.Part.from_bytes(
                data=image_content, mime_type="application/pdf"
            )
        else:
            if (
                len(image_content) > 1024 * 1024
            ):  # safety net if user uploads really large images
                image_content = prepare_image_for_gemini(image_content)
            part = types.Part.from_bytes(data=image_content, mime_type="image/jpeg")

        result = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[part],
            config=CONFIG,
        )

        final_data = (
            result.parsed.model_dump(mode="json")
            if result.parsed is not None
            else OCRResult.model_validate_json(result.text).model_dump(mode="json")
        )

        cache.set(cache_key, final_data, timeout=900)
        return final_data

    except Exception as exc:
        error_payload = {"error": "Failed to automatically extract document details."}
        cache.set(cache_key, error_payload, timeout=900)

        if isinstance(exc, requests.RequestException):
            raise GeminiOCRError(f"Failed to download document: {exc}") from exc
        raise GeminiOCRError(f"Gemini OCR failed: {exc}") from exc


@stashed_task
def delete_document(filepath: str) -> None:
    if filepath:
        s3.delete_object(Bucket=settings.R2_STORAGE_BUCKET_NAME, Key=filepath)


@stashed_task  # Scheduled task
def delete_orphaned_documents() -> None:
    grace_period = timezone.now() - timedelta(days=1)
    orphaned_files = DocumentData.objects.filter(
        associated_record=None, date_added__lt=grace_period
    )

    if not orphaned_files.exists():
        return

    file_data = list(orphaned_files.values_list("id", "filepath"))

    CHUNK_SIZE = 1000  # Cloudflare supports up to 1000 objects per deletion request
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
