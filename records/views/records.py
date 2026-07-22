import json
import logging
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.paginator import InvalidPage
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.views.generic.base import View
from django.views.generic.edit import CreateView, UpdateView
from django.views.generic.list import ListView
from django_filters.views import FilterView
from django_ratelimit.decorators import ratelimit

from documents.models import DocumentData, DocumentStatus
from documents.ocr_helpers import ocr_data_to_form_initial
from documents.tasks import extract_document
from Papertrail.responses import api_error
from Papertrail.utils import CachedPaginator

from ..filters import RecordFilter
from ..forms import AddRecordForm, RecordUpdateForm
from ..matching import try_match_document_record
from ..models import AuditLog, Folder, MergeLog, Record

logger = logging.getLogger(__name__)

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
    "payment_method",
    "nickname",
)


def _resolve_suggested_folder(user, initial: dict) -> dict:
    suggested = initial.pop("suggested_folder", None)
    if suggested:
        folder = Folder.objects.filter(user=user, name__iexact=suggested).first()
        if folder:
            initial["folder"] = folder.pk
    return initial


class RecordListView(LoginRequiredMixin, FilterView):
    model = Record
    template_name = "records/record_list_view.html"
    context_object_name = "records"
    filterset_class = RecordFilter
    paginate_by = settings.PAGINATE_BY

    @method_decorator(ratelimit(key="user", rate="120/m", method="GET", block=True))
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get_queryset(self):
        qs = Record.objects.for_user(self.request.user)
        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            return qs.smart_search(search_query).only(*LIST_FIELDS)
        return qs.only(*LIST_FIELDS).order_by("-last_edited")

    def get_template_names(self):
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

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return ["records/partials/record_form_partial.html"]
        return [self.template_name]

    def get_queryset(self):
        return Record.objects.for_user(self.request.user).with_documents()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        seven_years_ago = timezone.now() - timedelta(days=365 * 7)
        context["seven_years_ago_unix"] = seven_years_ago.timestamp()

        if self.object.is_plaid_record:
            active_merge = (
                MergeLog.objects.filter(
                    plaid_record=self.object,
                    undone_at__isnull=True,
                )
                .select_related("document_record", "document")
                .first()
            )
            if active_merge:
                context["active_merge"] = active_merge
                context["plaid_snapshot"] = active_merge.plaid_snapshot
                context["document_snapshot"] = active_merge.document_snapshot

        return context

    @transaction.atomic
    def form_valid(self, form):
        messages.success(self.request, "Record updated successfully.")
        self.object = form.save()

        if self.request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps(
                {"showToast": {"text": "Record updated successfully.", "tags": "success"}}
            )
            return response

        return redirect("records:record_detail", pk=self.object.pk)

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
                return api_error(
                    request,
                    "This document is already associated with a record.",
                    code="document_already_associated",
                    status=400,
                )

            if document.status in (
                DocumentStatus.UPLOADED,
                DocumentStatus.PENDING_UPLOAD,
                DocumentStatus.PROCESSING,
            ):
                cache_key = f"ocr_status_{document.id}"
                current_cached = cache.get(cache_key)
                if current_cached is None:
                    if document.did_ocr:
                        document.did_ocr = False
                        document.save(update_fields=["did_ocr"])
                    cache.set(cache_key, "processing", timeout=600)
                    extract_document.delay(document.id)

        return super().get(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

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
    def form_valid(self, form):
        document = self.document

        if document and document.associated_record:
            return api_error(
                self.request,
                "This document is already associated with a record.",
                code="document_already_associated",
                status=400,
            )

        self.object = form.save(commit=False)
        self.object.user = self.request.user
        self.object.save()
        form.save_m2m()

        if document:
            document.associated_record = self.object
            document.save(update_fields=["associated_record"])

        merged = try_match_document_record(self.object, document) if document else None
        if merged:
            messages.success(self.request, "Receipt matched with bank transaction and merged.")
            return redirect("records:record_detail", pk=merged.pk)

        return redirect("documents:add_support_docs", record_id=self.object.pk)


class CheckOCRStatus(LoginRequiredMixin, View):
    def get(self, request: HttpRequest, document_id: int) -> HttpResponse:
        document = DocumentData.objects.filter(id=document_id, user=request.user).first()
        if not document:
            raise Http404("Document not found.")

        if document.status in (DocumentStatus.COMPLETED, DocumentStatus.ERROR):
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
                    {"form": form, "is_waiting": False},
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
            {"is_waiting": True, "document_id": document_id},
        )


_HISTORY_EXCLUDE = frozenset(
    {
        "id",
        "user",
        "last_edited",
        "is_active",
        "expiry_notification_sent",
        "plaid_transaction_id",
        "plaid_item",
    }
)

_HISTORY_MAX_ENTRIES_PER_SOURCE = 200


class RecordHistoryView(LoginRequiredMixin, ListView):
    template_name = "records/record_history.html"
    context_object_name = "history"
    paginate_by = 25

    def get_queryset(self):
        from django.db.models import Q

        self._record = get_object_or_404(Record, pk=self.kwargs["pk"], user=self.request.user)

        record_entries = list(
            self._record.history.all()[:_HISTORY_MAX_ENTRIES_PER_SOURCE]
        )
        for h in record_entries:
            h.source_type = "record"

        doc_ids = set(self._record.documents.values_list("pk", flat=True))
        doc_ids.update(
            v["pk"]
            for v in DocumentData.history.filter(associated_record=self._record)
            .values("pk")
            .distinct()
        )

        doc_entries = (
            list(
                DocumentData.history.filter(pk__in=doc_ids)
                .select_related("history_user")[:_HISTORY_MAX_ENTRIES_PER_SOURCE]
            )
            if doc_ids
            else []
        )

        for h in doc_entries:
            h.source_type = "document"

        merged = record_entries + doc_entries

        merges = MergeLog.objects.filter(
            Q(plaid_record=self._record) | Q(document_record=self._record)
        )[:_HISTORY_MAX_ENTRIES_PER_SOURCE]
        for merge in merges:
            merge_entry = SimpleNamespace(
                source_type="merge",
                history_type="+",
                history_date=merge.created_at,
                history_user=None,
                merge=merge,
            )
            merged.append(merge_entry)
            if merge.undone_at:
                merged.append(
                    SimpleNamespace(
                        source_type="merge",
                        history_type="-",
                        history_date=merge.undone_at,
                        history_user=None,
                        merge=merge,
                    )
                )

        merged.sort(key=lambda x: x.history_date, reverse=True)
        return merged

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        record = self._record
        context["record"] = record
        context["tracked_fields"] = tuple(
            f.name
            for f in Record._meta.get_fields()
            if f.name not in _HISTORY_EXCLUDE
            and f.name != "folder"
            and not f.auto_created
            and not getattr(f, "is_relation", False)
        )
        context["doc_tracked_fields"] = tuple(
            f.name
            for f in DocumentData._meta.get_fields()
            if f.name
            not in (
                "id",
                "user",
                "filepath",
                "file_hash",
                "file_extension",
                "file_size",
                "mime_type",
                "ocr_retries",
                "ocr_error",
                "ocr_metadata",
                "status",
                "is_active",
                "created_at",
                "updated_at",
                "deleted_at",
            )
            and not f.auto_created
            and not getattr(f, "is_relation", False)
        )
        return context


class HardDeleteRecordView(LoginRequiredMixin, View):
    @method_decorator(ratelimit(key="user", rate="5/m", method="POST", block=True))
    def post(self, request, pk: int) -> HttpResponse:
        record = get_object_or_404(Record, pk=pk, user=request.user)
        seven_years_ago = timezone.now() - timedelta(days=365 * 7)
        if record.date_added > seven_years_ago:
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=204)
                response["HX-Trigger"] = json.dumps(
                    {
                        "showToast": {
                            "text": "This record is not old enough for permanent deletion.",
                            "tags": "error",
                        }
                    }
                )
                return response
            messages.error(request, "This record is not old enough for permanent deletion.")
            return redirect("records:record_detail", pk=pk)
        for doc in DocumentData.objects.filter(associated_record=record):
            doc.hard_delete()
        AuditLog.objects.create(
            user=request.user,
            action=AuditLog.Action.HARD_DELETE,
            record=record,
            details={"title": record.title},
        )
        record.hard_delete()
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps(
                {"showToast": {"text": "Record permanently deleted.", "tags": "success"}}
            )
            response["HX-Redirect"] = reverse("records:view_all_records")
            return response
        messages.success(request, "Record permanently deleted.")
        return redirect("records:view_all_records")
