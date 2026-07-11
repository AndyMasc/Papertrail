import json
import logging
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View
from django.views.generic import DeleteView, UpdateView

from records.models import Record
from .forms import R2UploadForm, DocumentUpdateForm
from .models import DocumentData
from .storage import generate_read_presigned_url, initiate_r2_upload

logger = logging.getLogger(__name__)


class UploadView(LoginRequiredMixin, View):
    def get(self, request):
        context = {
            "page_title": "Upload a financial record.",
            "page_subtitle": "We’ll extract and organize the details automatically.",
            "api_url": reverse("documents:upload_document"),
            "redirect_url_template": "/records/add_record/__ID__",
        }
        return render(request, "documents/upload_file.html", context)

    def post(self, request):
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        form = R2UploadForm(data)
        if not form.is_valid():
            return JsonResponse({"errors": form.errors}, status=400)

        try:
            result = initiate_r2_upload(
                request.user, 
                form.cleaned_data["filename"], 
                form.cleaned_data["content_type"]
            )
            return JsonResponse(result, safe=False)
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        except Exception as e:
            logger.error(f"R2 initialization failed for user {request.user.id}: {e}", exc_info=True)
            return JsonResponse({"error": "Internal server error initiating upload."}, status=500)


class ViewDocument(LoginRequiredMixin, UpdateView):
    model = DocumentData
    form_class = DocumentUpdateForm
    template_name = "documents/view_document.html"
    context_object_name = "document"

    def get_queryset(self):
        return DocumentData.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view_url"] = generate_read_presigned_url(self.object.filepath)
        return context

    def form_valid(self, form):
        form.save()
        return HttpResponse(status=204)

    def form_invalid(self, form):
        return HttpResponse("Invalid data submitted", status=400)


class DeleteDocument(LoginRequiredMixin, DeleteView):
    model = DocumentData

    def get_queryset(self):
        return self.model.objects.filter(user=self.request.user).select_related("associated_record")

    def get_success_url(self):
        associated_record = self.object.associated_record
        if associated_record:
            return reverse("records:record_detail", kwargs={"pk": associated_record.id})
        return reverse("records:view_all_records")

    def form_valid(self, form):
        try:
            with transaction.atomic():
                return super().form_valid(form)
        except Exception as e:
            logger.error(
                f"Failed to delete document {self.object.pk} for user {self.request.user.id}: {e}", 
                exc_info=True
            )
            return JsonResponse({"error": "Failed to complete deletion safely."}, status=500)


class AddSupportDocuments(LoginRequiredMixin, View):
    def get(self, request, record_id):
        record = get_object_or_404(Record, pk=record_id, user=request.user)
        context = {
            "record": record,
            "page_title": "Add supporting documents.",
            "page_subtitle": f"Attach additional records directly to {record.title}.",
            "api_url": reverse("documents:add_support_docs", kwargs={"record_id": record_id}),
            "redirect_url_template": f"/records/record_detail/{record_id}",
        }
        return render(request, "documents/upload_supporting_files.html", context)

    def post(self, request, record_id):
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        form = R2UploadForm(data)
        if not form.is_valid():
            return JsonResponse({"errors": form.errors}, status=400)

        if not Record.objects.filter(pk=record_id, user=request.user).exists():
            return JsonResponse({"error": "Record not found or unauthorized."}, status=404)

        try:
            result = initiate_r2_upload(
                user=request.user,
                filename=form.cleaned_data["filename"],
                content_type=form.cleaned_data["content_type"],
                record_id=record_id,
                notes=form.cleaned_data.get("notes", ""),
            )
            return JsonResponse(result)
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        except Exception as e:
            logger.error(f"Support doc upload initialization failed: {e}", exc_info=True)
            return JsonResponse({"error": "Internal server error initiating upload."}, status=500)