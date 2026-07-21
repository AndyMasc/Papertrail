from dataclasses import dataclass

from documents.models import DocumentData, DocumentStatus
from documents.storage import (
    gatekeeper_validate_r2_object,
    get_r2_object_head,
    verify_r2_object_exists,
)


@dataclass
class UploadValidationResult:
    valid: bool
    error: str | None = None
    file_size: int | None = None
    mime_type: str | None = None


class UploadValidator:
    def __init__(self, document: DocumentData, key: str):
        self.document = document
        self.key = key

    def validate(self) -> UploadValidationResult:
        if self.document.status != DocumentStatus.PENDING_UPLOAD:
            return UploadValidationResult(
                valid=False,
                error=f"Unexpected status: {self.document.status}.",
            )

        if self.document.filepath != self.key:
            return UploadValidationResult(
                valid=False,
                error="Key mismatch.",
            )

        if not verify_r2_object_exists(self.key):
            self.document.status = DocumentStatus.ERROR
            self.document.save(update_fields=["status"])
            return UploadValidationResult(
                valid=False,
                error="File not found in storage.",
            )

        validation = gatekeeper_validate_r2_object(self.key)
        if not validation["valid"]:
            self.document.status = DocumentStatus.ERROR
            self.document.notes = (
                (self.document.notes or "") + f"\n[Gatekeeper] {validation['error']}"
            ).strip()
            self.document.save(update_fields=["status", "notes"])
            return UploadValidationResult(
                valid=False,
                error=validation["error"],
            )

        head = get_r2_object_head(self.key)
        file_size = head.get("ContentLength") if head else None
        mime_type = head.get("ContentType", "").split(";")[0].strip() if head else ""

        return UploadValidationResult(
            valid=True,
            file_size=file_size,
            mime_type=mime_type,
        )
