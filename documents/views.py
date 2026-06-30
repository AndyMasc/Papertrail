import json
import uuid
from pathlib import Path

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView

from .models import Document_data
from .storage_helpers import generate_read_presigned_url, generate_write_presigned_url


class UploadView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, "documents/upload_file.html")

    def post(self, request):
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid JSON"}, status=400)

        filename = data.get("filename")
        content_type = data.get("content_type")
        if not filename or not content_type:
            return JsonResponse({"error": "missing fields"}, status=400)

        extension = Path(filename).suffix
        key = f"users/{request.user.id}/{uuid.uuid4()}{extension}"

        document = Document_data.objects.create(
            user=request.user,
            filepath=key,
        )
        upload_url = generate_write_presigned_url(key, content_type)

        return JsonResponse(
            {
                "upload_url": upload_url,
                "key": key,
                "document_id": document.id,
            }
        )


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
