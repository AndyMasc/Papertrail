import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponseBadRequest, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.functional import cached_property
from django.views.generic.base import View
from django.views.generic.edit import CreateView, DeleteView, UpdateView
from django_filters.views import FilterView

from documents.models import DocumentData
from documents.tasks import extract_document
from .filters import RecordFilter
from .forms import AddRecordForm, RecordUpdateForm
from .models import Record
from documents.ocr_helpers import ocr_data_to_form_initial
from django.conf import settings

paginate_by = settings.PAGINATE_BY

logger = logging.getLogger(__name__)


class RecordListView(LoginRequiredMixin, FilterView):
    model = Record
    template_name = "records/record_list_view.html"
    context_object_name = "records"
    filterset_class = RecordFilter
    paginate_by = settings.PAGINATE_BY

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .filter(user=self.request.user)
            .order_by("-last_edited")
        )
        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            return queryset.smart_search(search_query)
        return queryset

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

    @transaction.atomic
    def form_valid(self, form):
        self.object = form.save()
        if self.request.headers.get("HX-Request") == "true":
            return render(
                self.request,
                "records/partials/record_form_partial.html",
                self.get_context_data(form=form),
            )
        return super().form_valid(form)

    def form_invalid(self, form):
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
    def document(self):
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

            cache_key = f"ocr_status_{document.id}"
            current_status = cache.get(cache_key)
            if current_status is None:
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

            if cached_status == "processing" or cached_status is None:
                is_waiting = True

            elif isinstance(cached_status, dict):
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
            document.save()

            transaction.on_commit(lambda: cache.delete(f"ocr_status_{document.id}"))

        return redirect("documents:add_support_docs", record_id=self.object.id)


class CheckOCRStatus(LoginRequiredMixin, View):
    def get(self, request, document_id):
        if not DocumentData.objects.filter(id=document_id, user=request.user).exists():
            raise Http404("Document not found.")

        cache_key = f"ocr_status_{document_id}"
        data = cache.get(cache_key)

        # 1. Still processing
        if not isinstance(data, dict):
            return render(
                request,
                "records/partials/form_card.html",
                {
                    "is_waiting": True,
                    "document_id": document_id,
                },
            )

        # 2. Processed but failed
        if "error" in data:
            return render(
                request,
                "records/partials/form_card.html",
                {
                    "is_waiting": False,
                    "error_message": data["error"],
                    "form": AddRecordForm(),  # fallback to an empty form so they can manually type
                },
            )

        # 3. Processed successfully
        form = AddRecordForm(initial=ocr_data_to_form_initial(data))
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
        record = get_object_or_404(
            Record, id=record_id, user=request.user, is_active=True
        )
        record.is_active = False
        record.save(update_fields=["is_active"])
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
        return Record.objects.filter(user=self.request.user)
