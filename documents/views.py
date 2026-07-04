import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView
from records.models import Record

from .models import Document_data
from .storage_helpers import generate_read_presigned_url
from .upload_utils import initiate_r2_upload


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
            filename = data.get("filename")
            content_type = data.get("content_type")

            result = initiate_r2_upload(request.user, filename, content_type)
            return JsonResponse(result)

        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid JSON"}, status=400)
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)


class ViewDocument(LoginRequiredMixin, DetailView):
    model = Document_data
    template_name = "documents/view_document.html"
    context_object_name = "document"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view_url"] = generate_read_presigned_url(self.object.filepath)
        return context


class DeleteDocument(LoginRequiredMixin, View):
    model = Document_data
    context_object_name = "document"

    def post(self, request, pk):
        document = get_object_or_404(Document_data, user=request.user, pk=pk)
        try:
            document.delete()
        except Exception as e:
            return JsonResponse({"Error deleting from Cloudflare": str(e)}, status=500)
        return redirect("core:dashboard")


class AddSupportDocuments(LoginRequiredMixin, View):
    def get(self, request, record_id):
        record = get_object_or_404(Record, pk=record_id, user=request.user)
        context = {
            "record": record,
            "page_title": "Add supporting documents.",
            "page_subtitle": f"Attach additional records directly to {record.title}.",
            "api_url": reverse("documents:add_support_docs", kwargs={"record_id": record_id}),
            "redirect_url_template": "/records/record_detail/__ID__",
        }
        return render(request, "documents/upload_supporting_files.html", context)

    def post(self, request, record_id):
        try:
            data = json.loads(request.body or "{}")
            
            filename = data.get("filename")
            content_type = data.get("content_type")
            notes = data.get("notes")
            title = data.get("title")

            get_object_or_404(Record, pk=record_id, user=request.user)  # verify user owns the record

            result = initiate_r2_upload(
                user=request.user,
                filename=filename,
                content_type=content_type,
                record_id=record_id,
                notes=notes,
                title=title
            )
            return JsonResponse(result)

        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid JSON"}, status=400)
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
