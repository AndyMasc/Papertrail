"""Views for archiving, unarchiving, and deleting records.

Each action creates an AuditLog entry and, for HTMX requests, returns
a 204 response so the client can update the UI without a full page reload.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..models import AuditLog, Record
from ..services import (
    archive_record,
    bulk_archive_records,
    bulk_unarchive_records,
    unarchive_record,
)

logger = logging.getLogger(__name__)


class ArchiveRecord(LoginRequiredMixin, View):
    """Soft-delete a record by marking it inactive and logging the action."""

    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user, is_active=True)
        response = archive_record(record, request)
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.ARCHIVE,
            record=record,
        )
        if response:
            return response
        return redirect("records:view_all_records")


class UnarchiveRecord(LoginRequiredMixin, View):
    """Restore a soft-deleted record and log the action."""

    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user, is_active=False)
        unarchive_record(record)
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.UNARCHIVE,
            record=record,
        )
        return redirect("records:view_all_records")


class DeleteRecordView(LoginRequiredMixin, View):
    """Soft-delete a record and log the action."""

    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user)
        record.delete()
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.SOFT_DELETE,
            record=record,
        )
        return redirect("records:view_all_records")


@login_required
@ratelimit(key="user", rate="10/m", method="POST", block=True)
@require_POST
def BulkArchiveView(request: HttpRequest) -> HttpResponse:
    """Archive multiple records at once.

    Accepts a JSON body with ``{"record_ids": [1, 2, 3]}`` and archives
    all active records belonging to the user. Returns an HTMX-compatible
    response with the count of archived records.
    """
    try:
        data = json.loads(request.body)
        record_ids = data.get("record_ids", [])
    except (json.JSONDecodeError, AttributeError):
        return HttpResponse(
            '{"error": "Invalid request body"}', status=400, content_type="application/json"
        )

    if not isinstance(record_ids, list) or not all(isinstance(rid, int) for rid in record_ids):
        return HttpResponse(
            '{"error": "record_ids must be a list of integers"}',
            status=400,
            content_type="application/json",
        )

    count = bulk_archive_records(record_ids=record_ids, user=request.user)  # type: ignore[arg-type]

    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=200)
        response["HX-Trigger"] = json.dumps(
            {
                "recordChanged": {},
                "showToast": {
                    "message": f"{count} record{'s' if count != 1 else ''} archived.",
                    "tags": "success",
                },
            }
        )
        return response

    return redirect("records:view_all_records")


@login_required
@ratelimit(key="user", rate="10/m", method="POST", block=True)
@require_POST
def BulkUnarchiveView(request: HttpRequest) -> HttpResponse:
    """Restore multiple archived records at once.

    Accepts a JSON body with ``{"record_ids": [1, 2, 3]}`` and restores
    all inactive records belonging to the user. Returns an HTMX-compatible
    response with the count of restored records.
    """
    try:
        data = json.loads(request.body)
        record_ids = data.get("record_ids", [])
    except (json.JSONDecodeError, AttributeError):
        return HttpResponse(
            '{"error": "Invalid request body"}', status=400, content_type="application/json"
        )

    if not isinstance(record_ids, list) or not all(isinstance(rid, int) for rid in record_ids):
        return HttpResponse(
            '{"error": "record_ids must be a list of integers"}',
            status=400,
            content_type="application/json",
        )

    count = bulk_unarchive_records(record_ids=record_ids, user=request.user)  # type: ignore[arg-type]

    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=200)
        response["HX-Trigger"] = json.dumps(
            {
                "recordChanged": {},
                "showToast": {
                    "message": f"{count} record{'s' if count != 1 else ''} restored.",
                    "tags": "success",
                },
            }
        )
        return response

    return redirect("records:view_all_records")
