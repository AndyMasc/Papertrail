"""Thin service layer for record state transitions.

Keeps archive/unarchive logic out of views so it can be reused from
signals, tasks, or management commands without duplicating business rules.
"""

from django.http import HttpRequest, HttpResponse

from .models import Record


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
