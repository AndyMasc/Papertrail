import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.paginator import InvalidPage
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.views.generic.base import View
from django.views.generic.edit import CreateView, UpdateView
from django_filters.views import FilterView
from django_ratelimit.decorators import ratelimit

from documents.models import DocumentData, DocumentStatus
from documents.ocr_helpers import ocr_data_to_form_initial
from documents.tasks import extract_document
from Papertrail.utils import CachedPaginator

from ..filters import RecordFilter
from ..forms import AddRecordForm, RecordUpdateForm
from ..models import Folder, Record, RecordEvent, RecordQuerySet
from ..services import RecordCreator

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

    @transaction.atomic
    def form_valid(self, form):
        messages.success(self.request, "Record updated successfully.")

        if form.changed_data:
            field_event_map = {
                "merchant": RecordEvent.Event.MERCHANT_EDITED,
                "balance": RecordEvent.Event.AMOUNT_EDITED,
                "title": RecordEvent.Event.TITLE_EDITED,
                "record_type": RecordEvent.Event.RECORD_TYPE_CHANGED,
                "folder": RecordEvent.Event.FOLDER_CHANGED,
            }
            for field in form.changed_data:
                event = field_event_map.get(field)
                if event:
                    old_val = form.initial.get(field)
                    new_val = form.cleaned_data.get(field)
                    RecordEvent.objects.create(
                        record=self.object,
                        user=self.request.user,
                        event=event,
                        metadata={
                            "field": field,
                            "old": str(old_val) if old_val is not None else None,
                            "new": str(new_val) if new_val is not None else None,
                        },
                    )

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
                return HttpResponseBadRequest("This document is already associated with a record.")

            if document.status in (DocumentStatus.UPLOADED, DocumentStatus.PENDING_UPLOAD):
                cache_key = f"ocr_status_{document.id}"
                current_cached = cache.get(cache_key)
                if current_cached is None:
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

            if document.status in (DocumentStatus.PROCESSING, DocumentStatus.UPLOADED, DocumentStatus.PENDING_UPLOAD):
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
            return HttpResponseBadRequest("This document is already associated with a record.")

        creator = RecordCreator(
            user=self.request.user,
            form_data={**form.cleaned_data},
            document=document,
        )
        result = creator.create()
        self.object = result.record

        if result.was_merged:
            messages.success(self.request, "Receipt matched with bank transaction and merged.")
            return redirect("records:record_detail", pk=result.merged_record.pk)

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
