"""Thin service layer for record state transitions.

Keeps archive/unarchive logic out of views so it can be reused from
signals, tasks, or management commands without duplicating business rules.
"""

from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse

from .models import AuditLog, Record


def archive_record(record: Record, request: HttpRequest | None = None) -> HttpResponse | None:
    """Soft-delete *record* by marking it inactive.

    When called from an HTMX request, returns a 204 response with an
    ``HX-Trigger`` header so the front-end can react. For standard requests
    the caller should handle the redirect itself.
    """
    record.is_active = False
    record.save(update_fields=["is_active"])

    if request and request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=200)
        response["HX-Trigger"] = "recordChanged"
        return response

    return None


def unarchive_record(record: Record) -> None:
    """Restore a soft-deleted record by setting it active again."""
    record.is_active = True
    record.save(update_fields=["is_active"])


def bulk_archive_records(record_ids: list[int], user: User) -> int:
    """Archive multiple records by ID, scoped to the given user.

    Returns the number of records successfully archived.
    Each archived record creates an AuditLog entry.
    """
    records = Record.objects.filter(id__in=record_ids, user=user, is_active=True)
    count = 0
    for record in records:
        record.is_active = False
        record.save(update_fields=["is_active"])
        AuditLog.objects.create(
            user=user,
            action=AuditLog.Action.ARCHIVE,
            record=record,
        )
        count += 1
    return count


def bulk_unarchive_records(record_ids: list[int], user: User) -> int:
    """Unarchive multiple records by ID, scoped to the given user.

    Returns the number of records successfully restored.
    Each restored record creates an AuditLog entry.
    """
    records = Record.objects.filter(id__in=record_ids, user=user, is_active=False)
    count = 0
    for record in records:
        record.is_active = True
        record.save(update_fields=["is_active"])
        AuditLog.objects.create(
            user=user,
            action=AuditLog.Action.UNARCHIVE,
            record=record,
        )
        count += 1
    return count
