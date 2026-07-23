"""Upload confirmation service for transitioning documents after R2 upload.

Validates that the R2 object exists and passes gatekeeper checks, then
transitions the document from PENDING_UPLOAD to UPLOADED status.
"""

import logging
from dataclasses import dataclass

from documents.models import DocumentData, DocumentStatus
from documents.storage import gatekeeper_validate_r2_object, verify_r2_object_exists

logger = logging.getLogger(__name__)


@dataclass
class ConfirmationResult:
    """Outcome of an upload confirmation attempt."""

    success: bool = True
    error: str | None = None
    status_code: int = 200
    document: DocumentData | None = None


class ConfirmUploadService:
    """Validates and confirms a completed R2 upload, transitioning document status."""

    def __init__(self, document: DocumentData, key: str):
        self.document = document
        self.key = key

    def confirm(self) -> ConfirmationResult:
        """Run all validation checks and transition to UPLOADED on success.

        Checks key consistency, R2 object existence, and gatekeeper rules.
        Returns a ConfirmationResult indicating success or the specific failure.
        """
        if self.document.filepath != self.key:
            logger.warning(
                "Key mismatch for doc %s: expected=%s, received=%s",
                self.document.id,
                self.document.filepath,
                self.key,
            )
            return ConfirmationResult(success=False, error="Key mismatch.", status_code=400)

        if not verify_r2_object_exists(self.key):
            self.document.status = DocumentStatus.ERROR
            self.document.save(update_fields=["status"])
            return ConfirmationResult(
                success=False, error="File not found in storage.", status_code=404
            )

        validation = gatekeeper_validate_r2_object(self.key)
        if not validation["valid"]:
            self.document.status = DocumentStatus.ERROR
            self.document.notes = (
                (self.document.notes or "") + f"\n[Gatekeeper] {validation['error']}"
            ).strip()
            self.document.save(update_fields=["status", "notes"])
            logger.warning("Gatekeeper rejected doc %s: %s", self.document.id, validation["error"])
            return ConfirmationResult(success=False, error=validation["error"], status_code=422)

        self.document.status = DocumentStatus.UPLOADED
        self.document.save(update_fields=["status"])

        return ConfirmationResult(success=True, document=self.document)
