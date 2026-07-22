"""Document detail, delete, undo-delete, and hard-delete views."""

import logging
from datetime import timedelta
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import DatabaseError, transaction
from django.db.models import ProtectedError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import UpdateView

from Papertrail.views import htmx_response
from records.models import Record

from ..forms import DocumentUpdateForm
from ..models import DocumentData
from ..storage import generate_read_presigned_url

logger = logging.getLogger(__name__)


class ViewDocument(LoginRequiredMixin, UpdateView):
    """Document detail page with metadata editing and record association."""

    model = DocumentData
    form_class = DocumentUpdateForm
    template_name = "documents/view_document.html"
    context_object_name = "document"

    def get_template_names(self) -> list[str]:
        if self.request.headers.get("HX-Target") in [
            "search-results",
            "query-results-container",
        ]:
            return ["documents/partials/record_list_partial.html"]
        if self.request.headers.get("HX-Target") in [
            "document-form-container",
            "document-metadata-form",
        ]:
            return ["documents/partials/document_form_partial.html"]
        return [self.template_name]

    def get_queryset(self):
        return DocumentData.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["view_url"] = generate_read_presigned_url(self.object.filepath)
        seven_years_ago = timezone.now() - timedelta(days=365 * 7)
        context["seven_years_ago_unix"] = seven_years_ago.timestamp()

        records_list = self._search_records()
        paginator = Paginator(records_list, 5)
        page = self.request.GET.get("page", 1)

        try:
            page_obj = paginator.page(page)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        context["records"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()

        return context

    def _search_records(self):
        """Return user records matching the current search query for association."""
        queryset = Record.objects.for_user(self.request.user).active().only("id", "title")
        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            queryset = queryset.smart_search(search_query)
        return queryset

    @transaction.atomic
    def form_valid(self, form) -> HttpResponse:
        if "associated_record" in self.request.POST:
            record_id = self.request.POST.get("associated_record", "").strip()
            if not record_id:
                form.instance.associated_record = None
            else:
                record = get_object_or_404(Record, pk=record_id, user=self.request.user)
                form.instance.associated_record = record

        form.save()
        messages.success(self.request, "Updated successfully.")

        if self.request.headers.get("HX-Request") == "true":
            if "associated_record" in self.request.POST:
                redirect_url = reverse("documents:view_document", kwargs={"pk": self.object.pk})
                response = HttpResponse(status=204)
                response["HX-Redirect"] = redirect_url
                return response
            return HttpResponse(status=204)

        return redirect("documents:view_document", pk=self.object.pk)

    def form_invalid(self, form) -> HttpResponse:
        messages.error(self.request, "An error occurred.")
        if self.request.headers.get("HX-Request") == "true":
            return self.render_to_response(self.get_context_data(form=form), status=422)
        return super().form_invalid(form)


class DeleteDocument(LoginRequiredMixin, View):
    """Soft or hard-deletes a document and redirects to the parent record."""

    def post(self, request: HttpRequest, document_id: int) -> HttpResponse:
        document = get_object_or_404(
            DocumentData.objects.filter(user=request.user).with_record(),
            pk=document_id,
        )
        record = document.associated_record
        try:
            document.delete()
            if document.did_ocr:
                messages.info(
                    request,
                    "Document removed from record. Critical documents are preserved for compliance.",
                )
            else:
                messages.success(request, "Document deleted permanently.")
        except (ProtectedError, DatabaseError) as e:
            logger.error(
                "Failed to delete document %s for user %s: %s",
                document.pk,
                request.user.pk,
                e,
                exc_info=True,
            )
            messages.error(
                request,
                "Failed to complete deletion safely due to a system error.",
            )
            resp = htmx_response(request)
            if resp is not None:
                resp["HX-Refresh"] = "true"
                return resp
            return redirect(
                reverse("records:record_detail", kwargs={"pk": record.id})
                if record
                else reverse("records:view_all_records")
            )

        url = (
            reverse("records:record_detail", kwargs={"pk": record.id})
            if record
            else reverse("records:view_all_records")
        )
        return redirect(url)


class UndoDeleteDocument(LoginRequiredMixin, View):
    """Restores a soft-deleted document to active status."""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        document = get_object_or_404(DocumentData, pk=pk, user=request.user, is_active=False)
        document.undo_delete()
        resp = htmx_response(request, toast="Document restored.")
        if resp is not None:
            return resp
        messages.success(request, "Document restored.")
        return redirect("documents:trash_list")


class HardDeleteDocumentView(LoginRequiredMixin, View):
    """Permanently deletes documents older than 7 years from R2 and the database."""

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        document = get_object_or_404(DocumentData, pk=pk, user=request.user)
        seven_years_ago = timezone.now() - timedelta(days=365 * 7)
        if document.date_added > seven_years_ago:
            resp = htmx_response(
                request,
                toast="This document is not old enough for permanent deletion.",
                toast_tags="error",
            )
            if resp is not None:
                return resp
            messages.error(request, "This document is not old enough for permanent deletion.")
            return redirect("documents:view_document", pk=pk)

        filepath = document.filepath
        document.hard_delete()
        if filepath:
            from ..tasks import delete_document

            delete_document(filepath)

        resp = htmx_response(
            request,
            toast="Document permanently deleted.",
            redirect_url=reverse("documents:trash_list"),
        )
        if resp is not None:
            return resp
        messages.success(request, "Document permanently deleted.")
        return redirect("documents:trash_list")
