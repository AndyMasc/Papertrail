import logging
from django.core.cache import cache
import dateparser

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic.base import View
from django_filters.views import FilterView
from django.http import HttpResponse

from documents.models import Document_data
from documents.tasks import extract_document
from documents.storage_helpers import generate_read_presigned_url

from .filters import RecordFilter
from .forms import AddRecordForm, RecordUpdateForm
from .models import Record

logger = logging.getLogger(__name__)

MONTH_SHORTCUTS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


class RecordListView(LoginRequiredMixin, FilterView):
    model = Record
    template_name = "records/record_list_view.html"
    context_object_name = "records"
    filterset_class = RecordFilter

    def get_queryset(self):
        queryset = super().get_queryset().filter(user=self.request.user)
        search_query = self.request.GET.get("search", "").strip()

        if not search_query:
            return queryset

        conditions = [
            Q(title__icontains=search_query),
            Q(merchant__icontains=search_query),
            Q(products__icontains=search_query),
            Q(notes__icontains=search_query),
            Q(record_type__icontains=search_query),
        ]

        clean_numeric = ''.join(c for c in search_query if c.isdigit() or c == '.')
        if clean_numeric and clean_numeric.replace('.', '', 1).isdigit():
            conditions.append(Q(balance__gte=float(clean_numeric)) & Q(balance__lte=float(clean_numeric) + 0.99))

        parsed_date = dateparser.parse(
            search_query,
            settings={"PREFER_DATES_FROM": "past", "STRICT_PARSING": False},
        )

        if parsed_date:
            lower_query = search_query.lower()

            if search_query.isdigit() and len(search_query) == 4:
                conditions.extend([
                    Q(transaction_date__year=parsed_date.year),
                    Q(expiry_date__year=parsed_date.year),
                    Q(date_added__year=parsed_date.year),
                ])
            elif search_query.isalpha() and any(m in lower_query for m in MONTH_SHORTCUTS):
                conditions.extend([
                    Q(transaction_date__month=parsed_date.month),
                    Q(expiry_date__month=parsed_date.month),
                    Q(date_added__month=parsed_date.month),
                ])
            else:
                conditions.extend([
                    Q(transaction_date=parsed_date.date()),
                    Q(expiry_date=parsed_date.date()),
                    Q(date_added=parsed_date.date()),
                ])
                if not any(w in lower_query for w in ["today", "yesterday", "tomorrow"]):
                    conditions.extend([
                        Q(transaction_date__year=parsed_date.year, transaction_date__month=parsed_date.month),
                        Q(expiry_date__year=parsed_date.year, expiry_date__month=parsed_date.month),
                        Q(date_added__year=parsed_date.year, date_added__month=parsed_date.month),
                    ])

        final_filter = Q()
        for condition in conditions:
            final_filter |= condition

        return queryset.filter(final_filter).distinct()

    def get_template_names(self):
        if self.request.headers.get("HX-Target") == "query-results-container":
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

    def post(self, request, record_id): # If the form is edited in the detail view
        record = get_object_or_404(Record, user=request.user, pk=record_id)
        form = self.form_class(request.POST, instance=record)
        if form.is_valid():
            form.save()
            return HttpResponse(status=204)
        else:
            return render(request, self.template_name, {"record": record, "form": form}, status=422)


class AddRecord(LoginRequiredMixin, View):
    template_name = "records/add_record.html"

    def get_document(self, document_id, request):
        return get_object_or_404(
            Document_data.objects.select_related("associated_record"), 
            id=document_id, user=request.user
        )

    def get(self, request, document_id=None):
        document = None
        is_waiting = False
        
        if document_id:
            document = self.get_document(document_id, request)
            cache_key = f"ocr_data_{document_id}"
            cached_status = cache.get(cache_key) # grab whatever is currently in the cache

            if not cached_status: # If the cache is empty, set it to "processing" and extract the document
                cache.set(cache_key, "processing", timeout=300)
                
                extract_document.delay(
                    document.id, 
                    generate_read_presigned_url(document.filepath)
                )

            if cached_status == "processing" or not cached_status: # If the cache is empty or still processing, show waiting state
                is_waiting = True

        return render(
            request, self.template_name,
            {
                "document": document, 
                "form": AddRecordForm(),
                "is_waiting": is_waiting,
                "document_id": document_id,
            },
        )

    def post(self, request, document_id=None):
        document = self.get_document(document_id, request) if document_id else None
        
        if document and document.associated_record:
            return render(
                request, self.template_name, 
                {"error": "This document is already associated with a record."}, 
                status=400
            )

        form = AddRecordForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            record.user = request.user
            record.save()

            if document:
                document.associated_record = record
                document.save()

            cache.delete(f"ocr_result_{document_id}") # Clear cache since record saved in DB
            
            return redirect("documents:add_support_docs", record_id=record.id)
        return render(request, self.template_name, {"form": form, "document": document})


class CheckOCRStatus(LoginRequiredMixin, View):
    def get(self, request, document_id):
        cache_key = f"ocr_result_{document_id}"
        data = cache.get(cache_key)
        
        if data is None or data == "processing": # If the cache is empty or still processing, show waiting state
            return render(request, "records/partials/form_card.html", {
                "is_waiting": True, 
                "document_id": document_id,
            })

        initial = {
            "title": data.get("title"),
            "products": "\n".join(data.get("products") or []) if isinstance(data.get("products"), list) else data.get("products"),
            "merchant": data.get("merchant"),
            "balance": data.get("balance"),
            "transaction_date": data.get("transaction_date"),
            "expiry_date": data.get("expiry_date"),
            "record_type": data.get("record_type"),
        }
                
        form = AddRecordForm(initial=initial)
        return render(request, "records/partials/form_card.html", {
            "form": form,
            "is_waiting": False,
        })


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


class DeleteRecord(LoginRequiredMixin, View):
    def post(self, request, record_id):
        get_object_or_404(Record, id=record_id, user=request.user).delete()
        return redirect("records:view_all_records")