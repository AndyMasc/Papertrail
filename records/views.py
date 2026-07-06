import dateparser
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.db.models.fields import CharField
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic.base import View
from django.views.generic.list import ListView
from django_filters.views import FilterView
from documents.models import Document_data
from documents.scan_doc import extract_document
from documents.storage_helpers import generate_read_presigned_url
from django.http import HttpResponse
import json
from .filters import RecordFilter
from .forms import AddRecordForm, RecordUpdateForm
from .models import Record

MONTH_SHORTCUTS = [
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
]


class RecordListView(LoginRequiredMixin, FilterView):
    model = Record
    template_name = "records/record_list_view.html"
    context_object_name = "records"
    filterset_class = RecordFilter

    def get_queryset(self):
        # Prevent type-checker "unreachable" warning by separating execution steps
        base_queryset = super().get_queryset()
        queryset = base_queryset.filter(user=self.request.user)
        search_query = self.request.GET.get("search", "").strip()

        if not search_query:
            return queryset

        # Cast numeric balance field once up front for substring lookups
        queryset = queryset.annotate(
            balance_str=Cast("balance", output_field=CharField())
        )

        conditions = [
            Q(title__icontains=search_query),
            Q(merchant__icontains=search_query),
            Q(products__icontains=search_query),
            Q(notes__icontains=search_query),
            Q(record_type__icontains=search_query),
            Q(balance_str__contains=search_query),
        ]

        # Process structured natural language date matches
        parsed_date = dateparser.parse(
            search_query,
            settings={"PREFER_DATES_FROM": "past", "STRICT_PARSING": False},
        )

        if parsed_date:
            lower_query = search_query.lower()

            # Case A: 4-digit numeric year lookups (e.g., "2026")
            if search_query.isdigit() and len(search_query) == 4:
                conditions.extend(
                    [
                        Q(transaction_date__year=parsed_date.year),
                        Q(expiry_date__year=parsed_date.year),
                        Q(date_added__year=parsed_date.year),
                    ]
                )

            # Case B: Standard stand-alone month strings (e.g., "August")
            elif search_query.isalpha() and any(
                m in lower_query for m in MONTH_SHORTCUTS
            ):
                conditions.extend(
                    [
                        Q(transaction_date__month=parsed_date.month),
                        Q(expiry_date__month=parsed_date.month),
                        Q(date_added__month=parsed_date.month),
                    ]
                )

            # Case C: Single relative days or complete values (e.g., "yesterday", "July 5")
            else:
                conditions.extend(
                    [
                        Q(transaction_date=parsed_date.date()),
                        Q(expiry_date=parsed_date.date()),
                        Q(date_added=parsed_date.date()),
                    ]
                )

                # Dynamic calendar scope wrapper for composite text selections (e.g., "July 2026")
                if not any(
                    w in lower_query for w in ["today", "yesterday", "tomorrow"]
                ):
                    conditions.extend(
                        [
                            Q(
                                transaction_date__year=parsed_date.year,
                                transaction_date__month=parsed_date.month,
                            ),
                            Q(
                                expiry_date__year=parsed_date.year,
                                expiry_date__month=parsed_date.month,
                            ),
                            Q(
                                date_added__year=parsed_date.year,
                                date_added__month=parsed_date.month,
                            ),
                        ]
                    )

        # Fallback Substring Matching: Safely cast database dates to strings for structural lookups (e.g., "2026-07")
        if any(char.isdigit() for char in search_query):
            queryset = queryset.annotate(
                tx_date_str=Cast("transaction_date", output_field=CharField()),
                exp_date_str=Cast("expiry_date", output_field=CharField()),
                added_date_str=Cast("date_added", output_field=CharField()),
            )
            conditions.extend(
                [
                    Q(tx_date_str__contains=search_query),
                    Q(exp_date_str__contains=search_query),
                    Q(added_date_str__contains=search_query),
                ]
            )

        # Combine generated queries efficiently using OR logic gates
        final_filter = Q()
        for condition in conditions:
            final_filter |= condition

        return queryset.filter(final_filter)

    def get_template_names(self):
        if (
            "HX-Request" in self.request.headers
            and self.request.headers.get("HX-Target") == "query-results-container"
        ):
            return ["records/partials/record_list_partial.html"]
        return [self.template_name]


class RecordDetailView(LoginRequiredMixin, View):
    template_name = "records/record_detail_view.html"
    form_class = RecordUpdateForm

    def get(self, request, record_id):
        record = get_object_or_404(Record, user=request.user, pk=record_id)
        return render(
            request,
            self.template_name,
            {"record": record, "form": self.form_class(instance=record)},
        )

    def post(self, request, record_id):
        record = get_object_or_404(Record, user=request.user, pk=record_id)
        form = self.form_class(request.POST, instance=record)
        if form.is_valid():
            form.save()
            response = HttpResponse(status=204)
            
        # If invalid, re-render form section or return a 422 error
        return render(request, self.template_name, {"record": record, "form": form}, status=422)


class AddRecord(LoginRequiredMixin, View):
    template_name = "records/add_record.html"

    def get_document(self, document_id, request):
        return get_object_or_404(Document_data, id=document_id, user=request.user)

    def get(self, request, document_id=None):
        document = None
        initial = {}

        if document_id:
            document = self.get_document(document_id, request)
            try:
                ocr_result = extract_document(
                    generate_read_presigned_url(document.filepath)
                )
                data = ocr_result.model_dump()
                initial = {
                    "title": data.get("title"),
                    "products": "\n".join(data.get("products") or []),
                    "merchant": data.get("merchant"),
                    "balance": data.get("balance"),
                    "transaction_date": data.get("transaction_date"),
                    "expiry_date": data.get("expiry_date"),
                    "record_type": data.get("record_type"),
                }
            except Exception as e:
                return render(
                    request,
                    self.template_name,
                    {
                        "form": AddRecordForm(initial=initial),
                        "document": document,
                        "error": str(e),
                    },
                )

        return render(
            request,
            self.template_name,
            {"form": AddRecordForm(initial=initial), "document": document},
        )

    def post(self, request, document_id=None):
        document = self.get_document(document_id, request) if document_id else None
        if document and document.associated_record:
            return render(
                request,
                self.template_name,
                {"error": "This document is already associated with a record."},
            )

        form = AddRecordForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            record.user = request.user
            record.save()

            if document:
                document.associated_record = record
                document.save()

            return redirect("documents:add_support_docs", record_id=record.id)
        return render(request, self.template_name, {"form": form, "document": document})


class ArchiveRecord(LoginRequiredMixin, ListView):
    template_name = "records/record_list_view.html"
    context_object_name = "records"

    def post(self, request, record_id):
        record = get_object_or_404(Record, id=record_id, user=request.user)
        record.is_active = False
        record.save()
        return redirect("records:view_all_records")


class UnarchiveRecord(LoginRequiredMixin, View):
    def get(self, request, record_id):
        record = get_object_or_404(
            Record, id=record_id, user=request.user, is_active=False
        )
        record.is_active = True
        record.save()
        return redirect("records:view_all_records")


class DeleteRecord(LoginRequiredMixin, View):
    def post(self, request, record_id):
        get_object_or_404(Record, id=record_id, user=request.user).delete()
        return redirect("records:view_all_records")
