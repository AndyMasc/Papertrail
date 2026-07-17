import logging

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.functional import cached_property
from django.views.generic.base import View
from django.views.generic.edit import CreateView, DeleteView, UpdateView
from django_filters.views import FilterView
from django.http import HttpResponse

from django.contrib import messages
from documents.models import DocumentData, DocumentStatus
from documents.ocr_helpers import ocr_data_to_form_initial
from documents.tasks import extract_document

from .filters import RecordFilter
from .forms import AddRecordForm, RecordUpdateForm
from .models import Record

logger = logging.getLogger(__name__)

LIST_FIELDS = (
    "pk",
    "is_active",
    "record_type",
    "title",
    "merchant",
    "products",
    "expiry_date",
    "transaction_date",
    "date_added",
    "balance",
    "last_edited",
)


class RecordListView(LoginRequiredMixin, FilterView):
    model = Record
    template_name = "records/record_list_view.html"
    context_object_name = "records"
    filterset_class = RecordFilter
    paginate_by = settings.PAGINATE_BY

    def get_queryset(self):
        qs = Record.objects.for_user(self.request.user).order_by("-last_edited")
        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            return qs.smart_search(search_query)
        return qs.only(*LIST_FIELDS).order_by("-last_edited")

    def get_template_names(self):
        if self.request.headers.get("HX-Target") == "query-results-container":
            return ["records/partials/record_list_partial.html"]
        return [self.template_name]


class RecordDetailView(LoginRequiredMixin, UpdateView):
    template_name = "records/record_detail_view.html"
    form_class = RecordUpdateForm
    model = Record
    pk_url_kwarg = "pk"
    context_object_name = "record"

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return ["records/partials/record_form_partial.html"]
        return [self.template_name]

    def get_queryset(self):
        return Record.objects.for_user(self.request.user)

    @transaction.atomic
    def form_valid(self, form):
        messages.success(self.request, "Record updated successfully.")
        self.object = form.save()

        if self.request.headers.get("HX-Request") == "true":
            return render(
                self.request,
                "records/partials/record_form_partial.html",
                self.get_context_data(form=form),
            )
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "An error was left in a record")

        return render(
            self.request,
            self.get_template_names()[0],
            self.get_context_data(form=form),
            status=422,
        )


class AddRecordView(LoginRequiredMixin, CreateView):
    template_name = "records/add_record.html"
    model = Record
    form_class = AddRecordForm

    @cached_property
    def document(self) -> DocumentData | None:
        document_id = self.kwargs.get("document_id")
        if not document_id:
            return None
        return get_object_or_404(
            DocumentData.objects.select_related("associated_record"),
            id=document_id,
            user=self.request.user,
        )

    def get(self, request, *args, **kwargs):
        document = self.document
        if document:
            if document.associated_record:
                return HttpResponseBadRequest(
                    "This document is already associated with a record."
                )

            if document.status in (
                DocumentStatus.UPLOADED,
                DocumentStatus.PENDING_UPLOAD,
            ):
                cache_key = f"ocr_status_{document.id}"
                current_cached = cache.get(cache_key)
                if current_cached is None:
                    cache.set(cache_key, "processing", timeout=600)
                    extract_document.delay(document.id)

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.document

        is_waiting = False
        form = context.get("form") or self.get_form()

        if document:
            cache_key = f"ocr_status_{document.id}"
            cached_status = cache.get(cache_key)

            if document.status in (
                DocumentStatus.PROCESSING,
                DocumentStatus.UPLOADED,
                DocumentStatus.PENDING_UPLOAD,
            ):
                is_waiting = True

            elif document.status == DocumentStatus.ERROR:
                is_waiting = False
                error_data = cached_status if isinstance(cached_status, dict) else {}
                if isinstance(error_data, dict) and "error" in error_data:
                    context["error_message"] = error_data["error"]
                form = self.form_class()

            elif document.status == DocumentStatus.COMPLETED and isinstance(
                cached_status, dict
            ):
                is_waiting = False
                form = self.form_class(initial=ocr_data_to_form_initial(cached_status))

        context.update(
            {
                "form": form,
                "document": document,
                "document_id": self.kwargs.get("document_id"),
                "is_waiting": is_waiting,
            }
        )
        return context

    @transaction.atomic
    def form_valid(self, form):
        document = self.document

        if document and document.associated_record:
            return HttpResponseBadRequest(
                "This document is already associated with a record."
            )

        self.object = form.save(commit=False)
        self.object.user = self.request.user
        self.object.save()

        if document:
            document.associated_record = self.object
            document.save(update_fields=["associated_record"])

            transaction.on_commit(lambda: cache.delete(f"ocr_status_{document.id}"))

        return redirect("documents:add_support_docs", record_id=self.object.id)


class CheckOCRStatus(LoginRequiredMixin, View):
    def get(self, request, document_id):
        document = DocumentData.objects.filter(
            id=document_id, user=request.user
        ).first()
        if not document:
            raise Http404("Document not found.")

        if document.status in (
            DocumentStatus.COMPLETED,
            DocumentStatus.ERROR,
        ):
            cache_key = f"ocr_status_{document_id}"
            data = cache.get(cache_key)

            if document.status == DocumentStatus.ERROR:
                error_msg = (
                    data.get("error", "Extraction failed.")
                    if isinstance(data, dict)
                    else "Extraction failed."
                )
                return render(
                    request,
                    "records/partials/form_card.html",
                    {
                        "is_waiting": False,
                        "error_message": error_msg,
                        "form": AddRecordForm(),
                    },
                )

            if isinstance(data, dict) and "error" not in data:
                form = AddRecordForm(initial=ocr_data_to_form_initial(data))
                return render(
                    request,
                    "records/partials/form_card.html",
                    {
                        "form": form,
                        "is_waiting": False,
                    },
                )

            return render(
                request,
                "records/partials/form_card.html",
                {
                    "is_waiting": False,
                    "error_message": "Extraction produced no data. Please enter details manually.",
                    "form": AddRecordForm(),
                },
            )

        return render(
            request,
            "records/partials/form_card.html",
            {
                "is_waiting": True,
                "document_id": document_id,
            },
        )


class ArchiveRecord(LoginRequiredMixin, View):
    def post(self, request, record_id):
        record = get_object_or_404(
            Record, id=record_id, user=request.user, is_active=True
        )
        record.is_active = False
        record.save(update_fields=["is_active"])

        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=200)
            response["HX-Trigger"] = "recordChanged"
            return response

        return redirect("records:view_all_records")


class UnarchiveRecord(LoginRequiredMixin, View):
    def post(self, request, record_id):
        record = get_object_or_404(
            Record, id=record_id, user=request.user, is_active=False
        )
        record.is_active = True
        record.save(update_fields=["is_active"])
        return redirect("records:view_all_records")


class DeleteRecord(LoginRequiredMixin, DeleteView):
    model = Record
    success_url = reverse_lazy("records:view_all_records")

    def get_queryset(self):
        return Record.objects.for_user(self.request.user)
