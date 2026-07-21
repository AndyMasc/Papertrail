import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic.base import View

from ..models import Record
from ..services import archive_record, unarchive_record

logger = logging.getLogger(__name__)


class ArchiveRecord(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user, is_active=True)
        response = archive_record(record, request)
        if response:
            return response
        return redirect("records:view_all_records")


class UnarchiveRecord(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user, is_active=False)
        unarchive_record(record)
        return redirect("records:view_all_records")
