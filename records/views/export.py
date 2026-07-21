import csv
import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.generic.base import View

from ..models import Record

logger = logging.getLogger(__name__)


class RecordAuditExportView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, pk: int) -> HttpResponse:
        record = get_object_or_404(
            Record.objects.for_user(request.user).with_documents(),
            pk=pk,
        )
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="record_{record.pk}_audit.csv"'
        writer = csv.writer(response)

        writer.writerow(["Section", "Field", "Value"])
        writer.writerow(["Record", "ID", record.pk])
        writer.writerow(["Record", "Title", record.title])
        writer.writerow(["Record", "Merchant", record.merchant])
        writer.writerow(["Record", "Amount", str(record.balance) if record.balance else ""])
        writer.writerow(["Record", "Type", record.record_type])
        writer.writerow(["Record", "Source", record.source_type])
        writer.writerow(["Record", "Folder", record.folder.name if record.folder else ""])
        writer.writerow(["Record", "Transaction Date", str(record.transaction_date or "")])
        writer.writerow(["Record", "Expiry Date", str(record.expiry_date or "")])
        writer.writerow(["Record", "Created", str(record.date_added)])
        writer.writerow(["Record", "Last Edited", str(record.last_edited)])
        writer.writerow(["Record", "Soft-Deleted", str(record.deleted_at or "")])
        writer.writerow([])

        for doc in record.documents.all():
            writer.writerow(["Document", "ID", doc.id])
            writer.writerow(["Document", "Title", doc.title])
            writer.writerow(["Document", "File", doc.filepath])
            writer.writerow(["Document", "Size", str(doc.file_size or "")])
            writer.writerow(["Document", "MIME", doc.mime_type])
            writer.writerow(["Document", "Hash", doc.file_hash])
            writer.writerow(["Document", "OCR Complete", str(doc.did_ocr)])
            writer.writerow([])

        events = record.events.select_related("user").all()
        writer.writerow(["Event", "Timestamp", "User", "Type", "Metadata"])
        for e in events:
            user_email = e.user.email if e.user else "system"
            writer.writerow(
                [
                    "Event",
                    e.timestamp.isoformat(),
                    user_email,
                    e.get_event_display(),
                    json.dumps(e.metadata) if e.metadata else "",
                ]
            )
        writer.writerow([])

        writer.writerow(["Evidence", "Field", "Value"])
        if record.original_plaid:
            writer.writerow(["Evidence", "Original Plaid JSON", json.dumps(record.original_plaid)])
        if record.original_data:
            writer.writerow(["Evidence", "Original OCR Data", json.dumps(record.original_data)])

        return response
