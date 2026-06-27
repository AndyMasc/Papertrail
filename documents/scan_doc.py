import mimetypes

import requests
from django.conf import settings
from google import genai
from google.genai import types
from pydantic import BaseModel

client = genai.Client(api_key=settings.GEMINI_API_KEY)


class OCRResult(BaseModel):
    title: str
    merchant: str | None = None
    balance: float | None = None
    product: str | None = None
    transaction_date: str | None = None
    expiry_date: str | None = None
    record_type: str


CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=OCRResult,
)


PROMPT = """
You are an expert document extraction system.
Extract information from the attached purchase document.
Return structured data matching the provided schema.

Rules:
- Never invent information.
- Unknown values must be null.
- Dates must use YYYY-MM-DD.
- Balance must be a number only (no currency symbols).
- Choose exactly one record type:
- If a title cannot be extracted, set the title as "untitled". If a record type cannot be determined, set it as "other".

Record type must be one of:
- expense_receipt
- vendor_invoice
- purchase_order
- service_contract
- corporate_credit
- tax_document
- gift_voucher
- other

schema:
{
  "title": string,
  "merchant": string | null,
  "balance": number | null,
  "product": string | null,
  "transaction_date": string | null,
  "expiry_date": string | null,
  "record_type": string
}
"""


class GeminiOCRError(Exception):
    """Raised when Gemini OCR fails."""


def extract_document(signed_url: str) -> OCRResult:
    """
    Downloads a document from Cloudflare R2 and extracts
    structured information using Gemini.

    Returns:
        OCRResult
    """

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
                types.Part.from_bytes(
                    data=response.content,
                    mime_type=mime_type,
                ),
            ],
            config=CONFIG,
        )

        # Preferred: Gemini returns a parsed Pydantic model
        if result.parsed is not None:
            return result.parsed

        # Fallback (older SDK versions)
        return OCRResult.model_validate_json(result.text)

    except requests.RequestException as e:
        raise GeminiOCRError(f"Failed to download document: {exc}") from exc

    except Exception as exc:
        raise GeminiOCRError(f"Gemini OCR failed: {exc}") from exc
