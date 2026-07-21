import json
import logging
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.paginator import InvalidPage, Paginator
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.views.generic import ListView
from django.views.generic.base import View
from django.views.generic.edit import CreateView, DeleteView, FormView, UpdateView
from django_filters.views import FilterView
from django_ratelimit.decorators import ratelimit

from documents.models import DocumentData, DocumentStatus
from documents.ocr_helpers import ocr_data_to_form_initial
from documents.tasks import extract_document
from Papertrail.utils import CachedPaginator

from .filters import MergeLogFilter, RecordFilter
from .forms import AddRecordForm, FolderForm, ManualMergeForm, RecordUpdateForm
from .matching import merge_document_into_plaid, try_match_document_record, undo_merge
from .models import Folder, MergeLog, Record, RecordQuerySet
from .services import archive_record, unarchive_record

logger = logging.getLogger(__name__)


def _resolve_suggested_folder(user, initial: dict) -> dict:
    suggested = initial.pop("suggested_folder", None)
    if suggested:
        folder = Folder.objects.filter(user=user, name__iexact=suggested).first()
        if folder:
            initial["folder"] = folder.pk
    return initial


LIST_FIELDS = (
    "pk",
    "is_active",
    "record_type",
    "title",
    "merchant",
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

    @method_decorator(ratelimit(key="user", rate="120/m", method="GET", block=True))
    def dispatch(self, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(*args, **kwargs)

    def get_queryset(self) -> RecordQuerySet:
        qs = Record.objects.for_user(self.request.user)
        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            return qs.smart_search(search_query).only(*LIST_FIELDS)
        return qs.only(*LIST_FIELDS).order_by("-last_edited")

    def get_template_names(self) -> list[str]:
        if self.request.headers.get("HX-Target") == "query-results-container":
            return ["records/partials/record_list_partial.html"]
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


class RecordDetailView(LoginRequiredMixin, UpdateView):
    template_name = "records/record_detail_view.html"
    form_class = RecordUpdateForm
    model = Record
    pk_url_kwarg = "pk"
    context_object_name = "record"

    def get_template_names(self) -> list[str]:
        if self.request.headers.get("HX-Request") == "true":
            return ["records/partials/record_form_partial.html"]
        return [self.template_name]

    def get_queryset(self) -> RecordQuerySet:
        return Record.objects.for_user(self.request.user).with_documents()

    @transaction.atomic
    def form_valid(self, form) -> HttpResponse:
        messages.success(self.request, "Record updated successfully.")
        self.object = form.save()

        if self.request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps(
                {
                    "showToast": {
                        "text": "Record updated successfully.",
                        "tags": "success",
                    }
                }
            )
            return response

        return redirect("records:record_detail", pk=self.object.pk)

    def form_invalid(self, form) -> HttpResponse:
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

    def get(self, request, *args: Any, **kwargs: Any) -> HttpResponse:
        document = self.document
        if document:
            if document.associated_record:
                return HttpResponseBadRequest("This document is already associated with a record.")

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

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
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
                form = AddRecordForm(user=self.request.user)

            elif document.status == DocumentStatus.COMPLETED and isinstance(cached_status, dict):
                is_waiting = False
                initial = ocr_data_to_form_initial(cached_status)
                _resolve_suggested_folder(self.request.user, initial)
                form = AddRecordForm(initial=initial, user=self.request.user)

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
    def form_valid(self, form) -> HttpResponse:
        document = self.document

        if document and document.associated_record:
            return HttpResponseBadRequest("This document is already associated with a record.")

        self.object = form.save(commit=False)
        self.object.user = self.request.user
        self.object.save()

        if document:
            document.associated_record = self.object
            document.save(update_fields=["associated_record"])

            transaction.on_commit(lambda: cache.delete(f"ocr_status_{document.id}"))

        merged = try_match_document_record(self.object, document)
        if merged:
            messages.success(
                self.request,
                "Receipt matched with bank transaction and merged.",
            )
            return redirect("records:record_detail", pk=merged.pk)

        return redirect("documents:add_support_docs", record_id=self.object.pk)


class CheckOCRStatus(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, document_id: int) -> HttpResponse:
        document = DocumentData.objects.filter(id=document_id, user=request.user).first()
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
                        "form": AddRecordForm(user=request.user),
                    },
                )

            if isinstance(data, dict) and "error" not in data:
                initial = ocr_data_to_form_initial(data)
                _resolve_suggested_folder(request.user, initial)
                form = AddRecordForm(initial=initial, user=request.user)
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
                    "form": AddRecordForm(user=request.user),
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
    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user, is_active=True)
        response = archive_record(record, request)
        if response:
            return response
        return redirect("records:view_all_records")


class UnarchiveRecord(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, record_id: int) -> HttpResponse:
        record = get_object_or_404(Record, id=record_id, user=request.user, is_active=False)
        unarchive_record(record)
        return redirect("records:view_all_records")


class DeleteRecord(LoginRequiredMixin, DeleteView):
    model = Record
    success_url = reverse_lazy("records:view_all_records")

    def get_queryset(self) -> RecordQuerySet:
        return Record.objects.for_user(self.request.user)


class FolderListView(LoginRequiredMixin, ListView):
    model = Folder
    template_name = "records/folders.html"
    context_object_name = "folders"
    ordering = ["-created_at"]
    paginate_by = 12

    def get_template_names(self) -> list[str]:
        if self.request.headers.get("HX-Request"):
            return ["records/partials/folder_list_partial.html"]
        return [self.template_name]

    def get_queryset(self):
        qs = Folder.objects.filter(user=self.request.user)

        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            qs = qs.filter(name__icontains=search_query)

        return qs.annotate(
            active_records_count=Count("records", filter=Q(records__is_active=True))
        ).order_by("-created_at")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)

        context["unfiled_count"] = self.request.user.records.filter(
            folder__isnull=True, is_active=True
        ).count()

        return context


class CreateFolder(LoginRequiredMixin, CreateView):
    model = Folder
    form_class = FolderForm
    template_name = "records/partials/create_folder_modal.html"

    def form_valid(self, form) -> HttpResponse:
        form.instance.user = self.request.user
        self.object = form.save()

        if self.request.headers.get("HX-Request"):
            folders = Folder.objects.filter(user=self.request.user).annotate(
                active_records_count=Count("records", filter=Q(records__is_active=True))
            )
            unfiled_count = self.request.user.records.filter(
                folder__isnull=True, is_active=True
            ).count()
            response = render(
                self.request,
                "records/partials/folder_list_partial.html",
                {"folders": folders, "unfiled_count": unfiled_count, "page_obj": None},
            )
            response["HX-Trigger"] = json.dumps({"closeModal": True})
            return response

        return super().form_valid(form)

    def form_invalid(self, form) -> HttpResponse:
        response = super().form_invalid(form)
        if self.request.headers.get("HX-Request"):
            response.status_code = 422
        return response


class FolderUpdateView(LoginRequiredMixin, UpdateView):
    model = Folder
    form_class = FolderForm
    template_name = "records/partials/edit_folder_inline.html"
    pk_url_kwarg = "folder_id"

    def get_queryset(self):
        return Folder.objects.filter(user=self.request.user).annotate(
            active_records_count=Count("records", filter=Q(records__is_active=True))
        )

    def form_valid(self, form) -> HttpResponse:
        self.object = form.save()

        if self.request.headers.get("HX-Request"):
            response = render(
                self.request,
                "records/partials/folder_item_partial.html",
                {"folder": self.object},
            )
            return response

        return super().form_valid(form)

    def form_invalid(self, form) -> HttpResponse:
        response = super().form_invalid(form)
        if self.request.headers.get("HX-Request"):
            response.status_code = 422
        return response


class FolderDeleteView(LoginRequiredMixin, DeleteView):
    model = Folder
    pk_url_kwarg = "folder_id"
    success_url = reverse_lazy("records:view_folders")

    def get_queryset(self):
        return Folder.objects.filter(user=self.request.user)

    def delete(self, request, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        folder = self.get_object()

        folder.records.update(folder=None)
        folder.delete()

        if request.headers.get("HX-Request"):
            folders = Folder.objects.filter(user=self.request.user).annotate(
                active_records_count=Count("records", filter=Q(records__is_active=True))
            )
            unfiled_count = request.user.records.filter(folder__isnull=True, is_active=True).count()
            return render(
                request,
                "records/partials/folder_list_partial.html",
                {"folders": folders, "unfiled_count": unfiled_count, "page_obj": None},
            )

        messages.info(request, "Folder deleted. Records unfiled.")
        return redirect(self.success_url)


MANUAL_MERGE_PAGE_SIZE = 10


def _get_merge_candidate_qs(request: HttpRequest, mode: str) -> QuerySet[Record]:
    if mode not in ("plaid", "doc"):
        raise ValueError("Invalid mode")

    cache_attr = f"_merge_qs_{mode}"
    cached = getattr(request, cache_attr, None)
    if cached is not None:
        return cached

    qs = Record.objects.for_user(request.user).filter(is_active=True).select_related("folder")
    if mode == "plaid":
        qs = qs.filter(plaid_transaction_id__isnull=False)
    else:
        qs = qs.filter(plaid_transaction_id__isnull=True)

    setattr(request, cache_attr, qs)
    return qs


_merge_mode_labels = {"plaid": "Bank Transaction", "doc": "Uploaded Receipt"}


class ManualMergeView(LoginRequiredMixin, FormView):
    template_name = "records/manual_merge.html"
    form_class = ManualMergeForm
    success_url = reverse_lazy("records:merge_list")

    @method_decorator(ratelimit(key="user", rate="30/h", method="POST", block=True))
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        plaid_record = get_object_or_404(
            Record,
            pk=form.cleaned_data["plaid_record_id"],
            user=self.request.user,
            plaid_transaction_id__isnull=False,
            is_active=True,
        )
        document_record = get_object_or_404(
            Record,
            pk=form.cleaned_data["document_record_id"],
            user=self.request.user,
            plaid_transaction_id__isnull=True,
            is_active=True,
        )
        document = DocumentData.objects.filter(associated_record=document_record).first()
        result = merge_document_into_plaid(plaid_record, document_record, document)
        if result is None:
            messages.error(
                self.request, "Could not merge — the receipt may have already been merged."
            )
        else:
            messages.success(self.request, "Records merged successfully.")
        return redirect(self.success_url)


class ManualMergeSearchView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, mode: str) -> HttpResponse:
        try:
            qs = _get_merge_candidate_qs(request, mode)
        except ValueError:
            return HttpResponseBadRequest("Invalid mode")

        search_query = request.GET.get("search", "").strip()
        if search_query:
            qs = qs.smart_search(search_query)

        filterset = RecordFilter(request.GET, queryset=qs, request=request)
        qs = filterset.qs

        paginator = Paginator(qs, MANUAL_MERGE_PAGE_SIZE)
        page_number = request.GET.get("page", 1)
        try:
            page_obj = paginator.get_page(page_number)
        except InvalidPage:
            page_obj = paginator.get_page(1)

        return render(
            request,
            "records/partials/merge_search_panel.html",
            {
                "records": page_obj.object_list,
                "mode": mode,
                "target_prefix": f"modal-{mode}",
                "page_obj": page_obj,
                "is_paginated": page_obj.has_other_pages(),
            },
        )


class ManualMergeModalView(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, mode: str) -> HttpResponse:
        try:
            qs = _get_merge_candidate_qs(request, mode)
        except ValueError:
            return HttpResponseBadRequest("Invalid mode")

        qs = qs.order_by("-transaction_date")
        paginator = Paginator(qs, MANUAL_MERGE_PAGE_SIZE)
        page_obj = paginator.get_page(1)

        filter_instance = RecordFilter(request=request, data=None, queryset=Record.objects.none())

        return render(
            request,
            "records/partials/merge_modal_content.html",
            {
                "records": page_obj.object_list,
                "mode": mode,
                "label": _merge_mode_labels.get(mode, "Record"),
                "filter": filter_instance,
                "page_obj": page_obj,
                "is_paginated": page_obj.has_other_pages(),
            },
        )


class MergeListView(LoginRequiredMixin, FilterView):
    model = MergeLog
    template_name = "records/merge_list.html"
    context_object_name = "merges"
    filterset_class = MergeLogFilter
    paginate_by = 25

    def get_queryset(self):
        return MergeLog.objects.filter(
            plaid_record__user=self.request.user,
            undone_at__isnull=True,
        ).select_related("plaid_record", "document_record", "document")

    def get_template_names(self):
        if self.request.headers.get("HX-Target") == "merge-list-container":
            return ["records/partials/merge_list_partial.html"]
        return [self.template_name]


class UndoMergeView(LoginRequiredMixin, View):
    def post(self, request, merge_id: int) -> HttpResponse:
        merge_log = get_object_or_404(
            MergeLog.objects.select_related("plaid_record", "document_record", "document"),
            pk=merge_id,
            plaid_record__user=request.user,
        )

        restored = undo_merge(merge_log)
        if restored is None:
            messages.info(request, "This merge was already undone.")
        else:
            messages.success(request, "Merge undone. Records and document restored.")

        if request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps(
                {"showToast": {"text": "Merge undone.", "tags": "success"}}
            )
            return response

        return redirect("records:merge_list")
