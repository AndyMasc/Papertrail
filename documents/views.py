import logging
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import EmptyPage, InvalidPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import ListView, UpdateView
from django_filters.views import FilterView
from django_ratelimit.decorators import ratelimit

from Papertrail.utils import CachedPaginator
from records.models import Record, RecordEvent

from .filters import DocumentFilter
from .forms import DocumentUpdateForm
from .models import DocumentData, DocumentStatus
from .services import UploadService, UploadValidator
from .storage import generate_read_presigned_url

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
        result = UploadService(request, record_id=record_id).handle()

        if result.status == "error":
            return JsonResponse(
                {"error": result.error, "details": result.error_details},
                status=400,
            )

        if result.status == "duplicate_confirmed":
            return JsonResponse(
                {
                    "status": "duplicate_confirmed",
                    "document_id": result.existing_document_id,
                    "record_id": result.existing_record_id,
                    "record_label": result.existing_record_label,
                    "record_url": result.existing_record_url,
                }
            )

        return JsonResponse(
            {
                "status": "upload_url",
                "upload_url": result.upload_url,
                "key": result.key,
                "document_id": result.document_id,
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

            result = UploadValidator(document, key).validate()
            if not result.valid:
                return JsonResponse({"error": result.error}, status=422)

            document.file_size = result.file_size
            document.mime_type = result.mime_type or ""
            document.status = DocumentStatus.UPLOADED
            document.save(update_fields=["status", "file_size", "mime_type"])

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
        return DocumentData.objects.for_user(self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["view_url"] = generate_read_presigned_url(self.object.filepath)

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


class DeleteDocument(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, document_id: int) -> HttpResponse:
        document = get_object_or_404(
            DocumentData.objects.select_related("associated_record"),
            id=document_id,
            user=request.user,
        )
        record = document.associated_record

        RecordEvent.objects.create(
            record=record,
            user=request.user,
            event=RecordEvent.Event.DOCUMENT_REMOVED,
            metadata={"document_id": document.id, "title": document.title},
        ) if record else None

        delete_record_too = (
            record and record.source_type == Record.SourceType.OCR and document.did_ocr
        )

        if delete_record_too:
            RecordEvent.objects.create(
                record=record,
                user=request.user,
                event=RecordEvent.Event.DELETED,
                metadata={"source_type": record.source_type, "reason": "primary_evidence_removed"},
            )
            record.deleted_at = timezone.now()
            record.save(update_fields=["deleted_at"])

        document.delete()

        messages.success(request, "Document deleted.")
        if record and not delete_record_too:
            return redirect("records:record_detail", pk=record.pk)
        return redirect("records:view_all_records")


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
