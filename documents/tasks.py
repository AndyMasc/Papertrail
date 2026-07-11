import mimetypes

import requests
from django.conf import settings
from django.core.cache import cache
from django_qstash import stashed_task
from google import genai
from google.genai import types
from pydantic import BaseModel
from records.models import Record

from .storage import s3


class OCRResult(BaseModel):
    title: str
    # Notes: Notes these should be written by the user personally, only if necessary - Not OCR-generated.
    merchant: str | None = None
    balance: float | None = None
    products: list[str] | None = None
    transaction_date: str | None = None
    expiry_date: str | None = None
    record_type: Record.RecordTypes


CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=OCRResult,
)


PROMPT = """
You are an expert document extraction system.
Analyze the attached financial document and extract the requested fields into the structured schema provided.

Strict Rules:
1. Accuracy First: Extract data exactly as it appears. Never hallucinate or invent information.
2. Generate a concise, descriptive title (2–5 words). Prefer the format "Merchant + Key Item" (e.g., "Apple AirPods Pro", "IKEA Desk"). If a product name is overly long, shorten it to its most recognizable form while preserving its identity (e.g., "ASUS ROG Swift OLED Gaming Monitor XG27AQDMG" → "ASUS ROG OLED Monitor"). If this format is not suitable (eg, for a warranty or loan document), choose the shortest title that clearly identifies the document. Avoid invoice numbers, dates, and generic terms like "Receipt" or "Tax Invoice" unless needed for clarity.
3. Missing Data: If data for ANY field cannot be found, leave it blank by setting it to null. (IMPORTANT)
4. Date Formatting: Convert all extracted dates to YYYY-MM-DD format.
4. Currency Formatting: Extract the balance as a clean number only. Do not include currency symbols (e.g., $, €, £) or commas.
5. Contextual Inference & Data Standardization: You may infer data ONLY when there is overwhelming visual or contextual evidence (e.g., identifying a merchant from a prominent logo like "PB Tech"). You are explicitly authorized to clean, fix typos, and expand shorthand abbreviations or truncated product descriptions found on receipts into full, readable product names (e.g., converting "Banan yogurt" to "Banana Yogurt"). ALL product names, should be converted to title case. If the evidence is weak, ambiguous, or a shorthand name cannot be confidently identified, default strictly to null.
6. Descriptions should be brief, concise, and to the point. If a valid description is not able to be produced, default to null.

Note:
- 'Balance' refers to the total monetary sum extracted or stated on the submitted document (not restricted to a "remaining" amount, like a voucher balance).
"""


class GeminiOCRError(Exception):
    """Raised when Gemini OCR fails."""


@stashed_task
def extract_document(
    document_id: int, signed_url: str
) -> dict:  # Change return type annotation to dict
    cache_key = f"ocr_result_{document_id}"

    if settings.DEBUG:
        import time
        time.sleep(4)  # Simulate a slow response
        mock_data = OCRResult(
            title="Mock Title",
            merchant="Mock Merchant",
            balance=125.50,
            products=["Mock product 1", "Mock product 2", "Mock product 3"],
            transaction_date="2026-01-01",
            expiry_date="2026-01-09",
            record_type=Record.RecordTypes.EXPENSE_RECEIPT,
        ).model_dump(mode="json")

        cache.set(
            cache_key, mock_data, timeout=900
        )  # Cache the mock data for 15 minutes to store prepopulated ocr data, without cluttering DB.
        return mock_data

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    try:
        response = requests.get(signed_url, timeout=30)
        response.raise_for_status()

        mime_type = (
            response.headers.get("Content-Type")
            or mimetypes.guess_type(signed_url)[0]
            or "application/octet-stream"
        )

        result = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                PROMPT,
                types.Part.from_bytes(data=response.content, mime_type=mime_type),
            ],
            config=CONFIG,
        )

        if result.parsed is not None:
            final_data = result.parsed.model_dump(mode="json")
        else:
            final_data = OCRResult.model_validate_json(result.text).model_dump(
                mode="json"
            )

        cache.set(
            cache_key, final_data, timeout=900
        )  # Cache the mock data for 15 minutes to store prepopulated ocr data, without cluttering DB.
        return final_data

    except requests.RequestException as e:
        raise GeminiOCRError(f"Failed to download document: {e}") from e
    except Exception as exc:
        raise GeminiOCRError(f"Gemini OCR failed: {exc}") from exc


@stashed_task
def delete_document(filepath: str) -> None:
    if filepath:
        s3.delete_object(Bucket=settings.R2_STORAGE_BUCKET_NAME, Key=filepath)
