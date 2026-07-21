from typing import Any

from django.core.cache import cache
from django.db import transaction

from documents.models import DocumentData
from records.matching import try_match_document_record
from records.models import Record, RecordEvent


class RecordCreationResult:
    def __init__(
        self,
        record: Record,
        merged_record: Record | None = None,
    ):
        self.record = record
        self.merged_record = merged_record

    @property
    def was_merged(self) -> bool:
        return self.merged_record is not None

    @property
    def effective_record(self) -> Record:
        return self.merged_record or self.record


class RecordCreator:
    def __init__(self, user: Any, form_data: dict[str, Any], document: DocumentData | None = None):
        self.user = user
        self.form_data = form_data
        self.document = document

    @transaction.atomic
    def create(self) -> RecordCreationResult:
        record = Record(user=self.user, **self.form_data)
        record.source_type = self._resolve_source_type()

        self._attach_ocr_evidence(record)
        record.save()

        if self.document:
            self._link_document(record)
            self._log_receipt_uploaded(record)

        self._log_created(record)

        merged = try_match_document_record(record, self.document)
        if merged:
            return RecordCreationResult(record=record, merged_record=merged)

        return RecordCreationResult(record=record)

    def _resolve_source_type(self) -> str:
        if self.document:
            return Record.SourceType.OCR
        return Record.SourceType.MANUAL

    def _attach_ocr_evidence(self, record: Record) -> None:
        if not self.document:
            return
        cache_key = f"ocr_status_{self.document.id}"
        ocr_data = cache.get(cache_key)
        if isinstance(ocr_data, dict) and "_metadata" in ocr_data:
            record.original_data = ocr_data.get("parsed")
            self.document.ocr_metadata = ocr_data.get("_metadata")

    def _link_document(self, record: Record) -> None:
        self.document.associated_record = record
        self.document.save(update_fields=["associated_record", "ocr_metadata"])
        transaction.on_commit(lambda: cache.delete(f"ocr_status_{self.document.id}"))

    def _log_receipt_uploaded(self, record: Record) -> None:
        RecordEvent.objects.create(
            record=record,
            user=self.user,
            event=RecordEvent.Event.RECEIPT_UPLOADED,
            metadata={"document_id": self.document.id},
        )

    def _log_created(self, record: Record) -> None:
        RecordEvent.objects.create(
            record=record,
            user=self.user,
            event=RecordEvent.Event.CREATED,
        )
