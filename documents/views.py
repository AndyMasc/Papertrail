import json
import uuid
from pathlib import Path

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View

from .storage import generate_write_presigned_url


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

        upload_url = generate_write_presigned_url(key, content_type)

        return JsonResponse(
            {
                "upload_url": upload_url,
                "key": key,
            }
        )
