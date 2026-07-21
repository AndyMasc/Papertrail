import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import InvalidPage, Paginator
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic.base import View
from django.views.generic.edit import FormView
from django_filters.views import FilterView
from django_ratelimit.decorators import ratelimit

from documents.models import DocumentData

from ..filters import MergeLogFilter, RecordFilter
from ..forms import ManualMergeForm
from ..matching import merge_document_into_plaid, undo_merge
from ..models import MergeLog, Record

logger = logging.getLogger(__name__)

MANUAL_MERGE_PAGE_SIZE = 10

_merge_mode_labels = {"plaid": "Bank Transaction", "doc": "Uploaded Receipt"}


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
