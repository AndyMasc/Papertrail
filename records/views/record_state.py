import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic.base import View

from ..models import Record, RecordEvent
from ..services import archive_record, unarchive_record

logger = logging.getLogger(__name__)


class ArchiveRecord(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user, is_active=True)
        RecordEvent.objects.create(
            record=record,
            user=request.user,
            event=RecordEvent.Event.ARCHIVED,
        )
        response = archive_record(record, request)
        if response:
            return response
        return redirect("records:view_all_records")


class UnarchiveRecord(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user, is_active=False)
        RecordEvent.objects.create(
            record=record,
            user=request.user,
            event=RecordEvent.Event.UNARCHIVED,
        )
        unarchive_record(record)
        return redirect("records:view_all_records")


class DeleteRecord(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user, deleted_at__isnull=True)
        RecordEvent.objects.create(
            record=record,
            user=request.user,
            event=RecordEvent.Event.DELETED,
            metadata={"source_type": record.source_type},
        )
        record.deleted_at = timezone.now()
        record.save(update_fields=["deleted_at"])
        messages.success(request, "Record deleted.")
        return redirect("records:view_all_records")


class UndeleteRecord(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user, deleted_at__isnull=False)
        record.deleted_at = None
        record.save(update_fields=["deleted_at"])
        messages.success(request, "Record restored.")
        return redirect("records:record_detail", pk=record.pk)
