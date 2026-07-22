import hashlib
import json
import logging
import os
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import EmptyPage, InvalidPage, PageNotAnInteger, Paginator
from django.db import DatabaseError, transaction
from django.db.models import ProtectedError
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import DeleteView, ListView, UpdateView
from django_filters.views import FilterView
from django_ratelimit.decorators import ratelimit

from Papertrail.utils import CachedPaginator
from records.models import Record

from .filters import DocumentFilter
from .forms import DocumentUpdateForm, R2UploadForm
from .models import DocumentData, DocumentStatus
from .storage import (
    gatekeeper_validate_r2_object,
    generate_presigned_post,
    generate_read_presigned_url,
    generate_upload_key,
    verify_r2_object_exists,
)

logger = logging.getLogger(__name__)

LIST_FIELDS = (
    "pk",
    "title",
    "did_ocr",
    "notes",
    "date_added",
    "filepath",
    "file_extension",
    "associated_record_id",
)


class DocumentListView(LoginRequiredMixin, FilterView):
    template_name = "documents/document_list.html"
    model = DocumentData
    context_object_name = "documents"
    filterset_class = DocumentFilter
    paginate_by = settings.PAGINATE_BY

    @method_decorator(ratelimit(key="user", rate="120/m", method="GET", block=True))
    def dispatch(self, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(*args, **kwargs)

    def get_queryset(self):
        qs = (
            DocumentData.objects.for_user(self.request.user)
            .with_record()
            .only(*LIST_FIELDS, "associated_record__title")
            .order_by("-date_added")
        )
        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            return qs.search(search_query)
        return qs

    def get_template_names(self) -> list[str]:
        if self.request.headers.get("HX-Target") == "query-results-container":
            return ["documents/partials/document_list_partial.html"]
        return [self.template_name]

    def paginate_queryset(self, queryset, page_size):
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


class BaseR2UploadView(LoginRequiredMixin, View):
    @method_decorator(ratelimit(key="user", rate="30/h", method="POST", block=True))
    def dispatch(self, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(*args, **kwargs)

    def _handle_presign_request(
        self, request: HttpRequest, record_id: int | None = None
    ) -> JsonResponse:
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({"error": "Invalid JSON."}, status=400)
        else:
            data = request.POST

        file_hash = data.get("file_hash", "").strip()
        filename = data.get("filename", "").strip()
        content_type = data.get("content_type", "").strip().split(";")[0].strip()
        notes = data.get("notes", "").strip()
        force_upload = data.get("force_upload") == "true"

        if not file_hash or not filename:
            return JsonResponse({"error": "Missing file_hash or filename."}, status=400)

        form = R2UploadForm({"filename": filename, "content_type": content_type, "notes": notes})
        if not form.is_valid():
            return JsonResponse(
                {"error": "Invalid file parameters.", "details": form.errors},
                status=400,
            )

        if not force_upload:
            existing_doc = (
                DocumentData.objects.filter(user=request.user, file_hash=file_hash)
                .exclude(status=DocumentStatus.DELETING)
                .with_record()
                .first()
            )
            if existing_doc:
                record_id_out = None
                record_label = "Unassociated Document"
                record_url = "#"
                if existing_doc.associated_record:
                    record_id_out = existing_doc.associated_record.id
                    record_label = getattr(
                        existing_doc.associated_record,
                        "title",
                        f"Record #{record_id_out}",
                    )
                    record_url = f"/records/record_detail/{record_id_out}"
                return JsonResponse(
                    {
                        "status": "duplicate_confirmed",
                        "document_id": existing_doc.id,
                        "record_id": record_id_out,
                        "record_label": record_label,
                        "record_url": record_url,
                    }
                )

        effective_hash = file_hash
        if force_upload:
            salt = f"-forced-{uuid.uuid4().hex}"
            effective_hash = hashlib.sha256((file_hash + salt).encode("utf-8")).hexdigest()

        ext = os.path.splitext(filename)[1].lower() or ".bin"
        safe_title = os.path.splitext(filename)[0]
        safe_title = safe_title.replace("_", " ").replace("-", " ").title()

        key = generate_upload_key(request.user.id, ext)

        with transaction.atomic():
            document = DocumentData.objects.create(
                user=request.user,
                filepath=key,
                associated_record_id=record_id,
                did_ocr=(record_id is None),
                title=safe_title,
                notes=notes,
                file_hash=effective_hash,
                status=DocumentStatus.PENDING_UPLOAD,
            )

        upload_url = generate_presigned_post(request.user.id, key, content_type)

        return JsonResponse(
            {
                "status": "upload_url",
                "upload_url": upload_url,
                "key": key,
                "document_id": document.id,
            }
        )


class UploadView(BaseR2UploadView):
    def get(self, request: HttpRequest) -> HttpResponse:
        context = {
            "page_title": "Upload a financial record.",
            "page_subtitle": "We'll extract and organize the details automatically.",
            "api_url": reverse("documents:upload_document"),
            "redirect_url_template": reverse("records:add_record", kwargs={"document_id": "0"}),
            "is_supporting_flow": False,
        }
        return render(request, "documents/upload_file.html", context)

    def post(self, request: HttpRequest) -> JsonResponse:
        return self._handle_presign_request(request)


class ConfirmUploadView(LoginRequiredMixin, View):
    @method_decorator(ratelimit(key="user", rate="60/h", method="POST", block=True))
    def dispatch(self, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(*args, **kwargs)

    def post(self, request: HttpRequest) -> JsonResponse:
        document_id = request.POST.get("document_id")
        key = request.POST.get("key", "").strip()

        if not document_id or not key:
            return JsonResponse({"error": "Missing document_id or key."}, status=400)

        with transaction.atomic():
            try:
                document = DocumentData.objects.select_for_update().get(
                    id=document_id,
                    user=request.user,
                )
            except DocumentData.DoesNotExist:
                return JsonResponse({"error": "Document not found."}, status=404)

            if document.status != DocumentStatus.PENDING_UPLOAD:
                return JsonResponse(
                    {"error": f"Unexpected status: {document.status}."},
                    status=409,
                )

            if document.filepath != key:
                logger.warning(
                    "Key mismatch for doc %s: expected=%s, received=%s",
                    document_id,
                    document.filepath,
                    key,
                )
                return JsonResponse({"error": "Key mismatch."}, status=400)

            if not verify_r2_object_exists(key):
                document.status = DocumentStatus.ERROR
                document.save(update_fields=["status"])
                return JsonResponse({"error": "File not found in storage."}, status=404)

            validation = gatekeeper_validate_r2_object(key)
            if not validation["valid"]:
                document.status = DocumentStatus.ERROR
                document.notes = (
                    (document.notes or "") + f"\n[Gatekeeper] {validation['error']}"
                ).strip()
                document.save(update_fields=["status", "notes"])
                logger.warning("Gatekeeper rejected doc %s: %s", document_id, validation["error"])
                return JsonResponse({"error": validation["error"]}, status=422)

            document.status = DocumentStatus.UPLOADED
            document.save(update_fields=["status"])

        return JsonResponse({"status": "confirmed", "document_id": document.id})


class ViewDocument(LoginRequiredMixin, UpdateView):
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

        records_list = self.search_records()
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

    def search_records(self):
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
        messages.error(self.request, "An error occured.")
        if self.request.headers.get("HX-Request") == "true":
            return self.render_to_response(self.get_context_data(form=form), status=422)
        return super().form_invalid(form)


class PendingOCRListView(LoginRequiredMixin, ListView):
    template_name = "documents/pending_ocr_list.html"
    model = DocumentData
    context_object_name = "documents"
    paginate_by = settings.PAGINATE_BY

    @method_decorator(ratelimit(key="user", rate="120/m", method="GET", block=True))
    def dispatch(self, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(*args, **kwargs)

    def get_queryset(self):
        return (
            DocumentData.objects.for_user(self.request.user)
            .filter(
                did_ocr=True,
                associated_record__isnull=True,
                status__in=[
                    DocumentStatus.UPLOADED,
                    DocumentStatus.PROCESSING,
                    DocumentStatus.COMPLETED,
                    DocumentStatus.ERROR,
                ],
            )
            .only("id", "title", "status", "date_added")
            .order_by("-date_added")
        )


class DeleteDocument(LoginRequiredMixin, DeleteView):
    model = DocumentData
    pk_url_kwarg = "document_id"

    def get_queryset(self):
        return self.model.objects.filter(user=self.request.user).with_record()

    def get_success_url(self) -> str:
        record = getattr(self, "_associated_record", None)
        if record:
            return reverse("records:record_detail", kwargs={"pk": record.id})
        return reverse("records:view_all_records")

    @transaction.atomic
    def form_valid(self, _form) -> HttpResponse:
        record = self.object.associated_record
        try:
            self.object.delete()
            if self.object.did_ocr:
                messages.info(
                    self.request,
                    "Document removed from record. Critical documents are preserved for compliance.",
                )
            else:
                messages.success(self.request, "Document deleted permanently.")
            url = (
                reverse("records:record_detail", kwargs={"pk": record.id})
                if record
                else reverse("records:view_all_records")
            )
            return redirect(url)
        except (ProtectedError, DatabaseError) as e:
            logger.error(
                "Failed to delete document %s for user %s: %s",
                self.object.pk,
                self.request.user.pk,
                e,
                exc_info=True,
            )
            messages.error(
                self.request,
                "Failed to complete deletion safely due to a system error.",
            )
            if self.request.headers.get("HX-Request") == "true":
                return HttpResponse(status=204, headers={"HX-Refresh": "true"})
            url = (
                reverse("records:record_detail", kwargs={"pk": record.id})
                if record
                else reverse("records:view_all_records")
            )
            return redirect(url)


class AddSupportDocuments(BaseR2UploadView):
    def get(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, pk=record_id, user=request.user)
        context = {
            "record": record,
            "page_title": "Add supporting documents.",
            "page_subtitle": f"Attach additional records directly to {record.title}.",
            "api_url": reverse("documents:add_support_docs", kwargs={"record_id": record_id}),
            "redirect_url_template": f"/records/record_detail/{record_id}",
            "is_supporting_flow": True,
        }
        return render(request, "documents/upload_supporting_files.html", context)

    def post(self, request: HttpRequest, record_id: int) -> JsonResponse:
        get_object_or_404(Record, pk=record_id, user=request.user)
        return self._handle_presign_request(request, record_id=record_id)


class TrashDocumentListView(DocumentListView):
    template_name = "documents/trash_list.html"

    def get_queryset(self):
        qs = (
            DocumentData.objects.filter(user=self.request.user, is_active=False)
            .with_record()
            .only(*LIST_FIELDS, "associated_record__title")
            .order_by("-deleted_at")
        )
        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            return qs.search(search_query)
        return qs

    def get_template_names(self) -> list[str]:
        if self.request.headers.get("HX-Target") == "query-results-container":
            return ["documents/partials/document_list_partial.html"]
        return [self.template_name]


class UndoDeleteDocument(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        document = get_object_or_404(DocumentData, pk=pk, user=request.user, is_active=False)
        document.undo_delete()
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=200)
            response["HX-Trigger"] = json.dumps(
                {"showToast": {"text": "Document restored.", "tags": "success"}}
            )
            return response
        messages.success(request, "Document restored.")
        return redirect("documents:trash_list")


class HardDeleteDocumentView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        document = get_object_or_404(DocumentData, pk=pk, user=request.user)
        seven_years_ago = timezone.now() - timedelta(days=365 * 7)
        if document.date_added > seven_years_ago:
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=204)
                response["HX-Trigger"] = json.dumps(
                    {
                        "showToast": {
                            "text": "This document is not old enough for permanent deletion.",
                            "tags": "error",
                        }
                    }
                )
                return response
            messages.error(request, "This document is not old enough for permanent deletion.")
            return redirect("documents:view_document", pk=pk)
        filepath = document.filepath
        document.hard_delete()
        if filepath:
            from .tasks import delete_document

            delete_document(filepath)
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps(
                {"showToast": {"text": "Document permanently deleted.", "tags": "success"}}
            )
            response["HX-Redirect"] = reverse("documents:trash_list")
            return response
        messages.success(request, "Document permanently deleted.")
        return redirect("documents:trash_list")
