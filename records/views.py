from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic.base import View
from django.views.generic.list import ListView
from documents.models import Document_data
from documents.scan_doc import extract_document
from documents.storage_helpers import generate_read_presigned_url

from .forms import AddRecordForm
from .models import Record


# Create your views here.
class RecordListView(LoginRequiredMixin, ListView):
    model = Record
    template_name = "records/view_all_records.html"
    context_object_name = "records"

    def get_queryset(self):
        return Record.objects.filter(user=self.request.user)


class AddRecord(LoginRequiredMixin, View):
    template_name = "records/add_record.html"

    def get_document(self, document_id, request):
        return get_object_or_404(Document_data, id=document_id, user=request.user)

    def get(self, request, document_id=None):
        document = None
        initial = {}

        if document_id:
            document = self.get_document(document_id, request)
            signed_url = generate_read_presigned_url(document.filepath)

            try:
                ocr_result = extract_document(signed_url)
                data = ocr_result.model_dump()

                products_list = data.get("products") or []
                products_text = "\n".join(products_list)

                initial = {
                    "title": data.get("title"),
                    "products": products_text,
                    "merchant": data.get("merchant"),
                    "balance": data.get("balance"),
                    "transaction_date": data.get("transaction_date"),
                    "expiry_date": data.get("expiry_date"),
                    "record_type": data.get("record_type"),
                }

            except Exception as e:
                context = {
                    "form": AddRecordForm(initial=initial),
                    "document": document,
                    "error": str(e),
                }
                return render(request, self.template_name, context)

        form = AddRecordForm(initial=initial)
        context = {"form": form, "document": document}
        return render(request, self.template_name, context)

    def post(self, request, document_id=None):
        if document_id:
            document = get_object_or_404(
                Document_data, id=document_id, user=request.user
            )
        else:
            document = None

        form = AddRecordForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            record.user = request.user
            record.associated_document = document

            record.save()
            return redirect("records:view_all_records")

        context = {"form": form, "document": document}
        return render(request, self.template_name, context)

class DeleteRecord(LoginRequiredMixin, View):
    def post(self, request, record_id):
        record = get_object_or_404(
            Record,
            id=record_id,
            user=request.user,
        )
        record.delete()
        return redirect("core:dashboard")