"""Shared view utilities and mixins for the Papertrail project.

Provides reusable helpers for HTMX responses, pagination, and audit
logging that are used across multiple Django apps.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.core.paginator import InvalidPage
from django.http import Http404, HttpRequest, HttpResponse

from Papertrail.utils import CachedPaginator

logger = logging.getLogger(__name__)


def htmx_response(
    request: HttpRequest,
    *,
    toast: str = "",
    toast_tags: str = "success",
    redirect_url: str = "",
    status: int = 204,
) -> HttpResponse | None:
    """Return an HTMX-aware response if the request is from HTMX, else None.

    When the caller is an HTMX request, returns a 204 with HX-Trigger toast
    (and optionally HX-Redirect).  When the caller is a normal browser request,
    returns None so the view can fall through to ``messages`` + ``redirect()``.

    Args:
        request: The incoming Django request.
        toast: Text shown in the toast notification.
        toast_tags: Tailwind toast class (``"success"`` or ``"error"``).
        redirect_url: If set, adds ``HX-Redirect`` to navigate the client.
        status: HTTP status code (default 204 No Content).
    """
    if request.headers.get("HX-Request") != "true":
        return None
    response = HttpResponse(status=status)
    if toast:
        response["HX-Trigger"] = json.dumps({"showToast": {"text": toast, "tags": toast_tags}})
    if redirect_url:
        response["HX-Redirect"] = redirect_url
    return response


class CachedPaginatorMixin:
    """Mixin that replaces paginate_queryset with a CachedPaginator.

    Avoids re-running expensive COUNT queries on repeated page requests.
    Use with ``ListView`` or ``FilterView`` that sets ``paginate_by``.
    """

    def paginate_queryset(self, queryset, page_size):  # type: ignore[override]
        paginator = CachedPaginator(queryset, page_size)
        page_kwarg = self.page_kwarg
        page = self.kwargs.get(page_kwarg) or self.request.GET.get(page_kwarg) or 1
        try:
            page_number = int(page)
        except ValueError:
            if page == "last":
                page_number = paginator.num_pages
            else:
                raise Http404 from None
        try:
            page = paginator.page(page_number)
            return (paginator, page, page.object_list, page.has_other_pages())
        except InvalidPage:
            raise Http404 from None


def create_audit_log(
    *,
    user: Any,
    action: Any,
    record: Any,
    merge_log: Any | None = None,
    details: dict | None = None,
) -> Any:
    """Create an AuditLog entry, reducing boilerplate in merge/detach views."""
    from records.models import AuditLog

    kwargs: dict[str, Any] = {
        "user": user,
        "action": action,
        "record": record,
    }
    if merge_log is not None:
        kwargs["merge_log"] = merge_log
    if details is not None:
        kwargs["details"] = details
    return AuditLog.objects.create(**kwargs)
