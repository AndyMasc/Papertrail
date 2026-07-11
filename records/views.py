import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic.base import View
from django.views.generic.edit import CreateView, DeleteView, UpdateView
from django_filters.views import FilterView
from documents.models import DocumentData
from documents.storage import generate_read_presigned_url
from documents.tasks import extract_document

from .filters import RecordFilter
from .forms import AddRecordForm, RecordUpdateForm
from .models import Record

logger = logging.getLogger(__name__)

class RecordListView(LoginRequiredMixin, FilterView):
    model = Record
    template_name = "records/record_list_view.html"
    context_object_name = "records"
    filterset_class = RecordFilter

    def get_queryset(self):
        queryset = super().get_queryset().filter(user=self.request.user)
        search_query = self.request.GET.get("search", "")
        return queryset.smart_search(search_query)

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
        return Record.objects.filter(user=self.request.user)

    def form_valid(self, form):
        if self.request.headers.get("HX-Request") == "true":
            self.object = form.save()
            return render(
                self.request, 
                "records/partials/record_form_partial.html", 
                self.get_context_data(form=form)
            )
        return super().form_valid(form)

    def form_invalid(self, form):
        return render(
            self.request, 
            self.get_template_names()[0], 
            self.get_context_data(form=form), 
            status=422
        )


class AddRecordView(LoginRequiredMixin, CreateView):
    template_name = "records/add_record.html"
    model = Record
    form_class = AddRecordForm

    def get_document(
        self,
    ):  # Helper to fetch the document if document_id is in the URL.
        document_id = self.kwargs.get("document_id")
        if not document_id:
            return None
        return get_object_or_404(
            DocumentData.objects.select_related("associated_record"),
            id=document_id,
            user=self.request.user,
        )

    def get(self, request, *args, **kwargs):
        document = self.get_document()
        if document:
            if document.associated_record:
                return HttpResponseBadRequest(
                    "This document is already associated with a record."
                )

            cache_key = f"ocr_data_{document.id}"
            cached_status = cache.get(cache_key)

            if not cached_status:
                cache.set(cache_key, "processing", timeout=300)
                extract_document.delay(
                    document.id, generate_read_presigned_url(document.filepath)
                )

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        document = self.get_document()

        is_waiting = False
        if document:
            cache_key = f"ocr_data_{document.id}"
            cached_status = cache.get(cache_key)
            if cached_status == "processing" or not cached_status:
                is_waiting = True

        context.update(
            {
                "document": document,
                "document_id": self.kwargs.get("document_id"),
                "is_waiting": is_waiting,
            }
        )
        return context

    def form_valid(self, form):
        document = self.get_document()

        if document and document.associated_record:
            return HttpResponseBadRequest(
                "This document is already associated with a record."
            )

        self.object = form.save(commit=False)
        self.object.user = self.request.user
        self.object.save()

        if document:
            document.associated_record = self.object
            document.save()
            cache.delete(f"ocr_data_{document.id}")  # Clear cache

        return redirect("documents:add_support_docs", record_id=self.object.id)


class CheckOCRStatus(LoginRequiredMixin, View):
    def get(self, request, document_id):
        cache_key = f"ocr_result_{document_id}"
        data = cache.get(cache_key)

        if (
            data is None or data == "processing"
        ):  # If the cache is empty or still processing, show waiting state
            return render(
                request,
                "records/partials/form_card.html",
                {
                    "is_waiting": True,
                    "document_id": document_id,
                },
            )

        initial = {
            "title": data.get("title"),
            "products": "\n".join(data.get("products") or [])
            if isinstance(data.get("products"), list)
            else data.get("products"),
            "merchant": data.get("merchant"),
            "balance": data.get("balance"),
            "transaction_date": data.get("transaction_date"),
            "expiry_date": data.get("expiry_date"),
            "record_type": data.get("record_type"),
        }

        form = AddRecordForm(initial=initial)
        return render(
            request,
            "records/partials/form_card.html",
            {
                "form": form,
                "is_waiting": False,
            },
        )


class ArchiveRecord(LoginRequiredMixin, View):
    def post(self, request, record_id):
        record = get_object_or_404(Record, id=record_id, user=request.user)
        record.is_active = False
        record.save()
        return redirect("records:view_all_records")


class UnarchiveRecord(LoginRequiredMixin, View):
    def post(self, request, record_id):
        record = get_object_or_404(
            Record, id=record_id, user=request.user, is_active=False
        )
        record.is_active = True
        record.save()
        return redirect("records:view_all_records")


class DeleteRecord(LoginRequiredMixin, DeleteView):
    model = Record
    success_url = reverse_lazy("records:view_all_records")

    def get_queryset(self):
        return Record.objects.filter(user=self.request.user)
