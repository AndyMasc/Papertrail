import json
import logging
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DeleteView, UpdateView
from django_filters.views import FilterView

from records.models import Record
from .filters import DocumentFilter
from .forms import R2UploadForm, DocumentUpdateForm
from .models import DocumentData
from .storage import generate_read_presigned_url, initiate_r2_upload
from django.conf import settings

logger = logging.getLogger(__name__)

paginate_by = settings.PAGINATE_BY

class DocumentListView(LoginRequiredMixin, FilterView):
    template_name = "documents/document_list.html"
    model = DocumentData
    context_object_name = "documents"
    filterset_class = DocumentFilter
    paginate_by = settings.PAGINATE_BY
    
    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .filter(user=self.request.user)
            .select_related("associated_record")
            .order_by("-date_added")
        )
        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            return queryset.search(search_query)
        return queryset

    def get_template_names(self):
        if self.request.headers.get("HX-Target") == "query-results-container":
            return ["documents/partials/document_list_partial.html"]
        return [self.template_name]
        

class BaseR2UploadView(LoginRequiredMixin, View): # Abstract helper class to centralize JSON extraction and Cloudflare R2 signature management.
    def _handle_r2_upload(self, request, record_id=None):
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON payload."}, status=400)

        form = R2UploadForm(data)
        if not form.is_valid():
            return JsonResponse({"errors": form.errors}, status=400)

        if record_id and not Record.objects.filter(pk=record_id, user=request.user).exists():
            return JsonResponse({"error": "Target record not found or unauthorized."}, status=404)

        try:
            upload_kwargs = {
                "user": request.user,
                "filename": form.cleaned_data["filename"],
                "content_type": form.cleaned_data["content_type"]
            }
            if record_id:
                upload_kwargs["record_id"] = record_id
                upload_kwargs["notes"] = form.cleaned_data.get("notes", "")

            result = initiate_r2_upload(**upload_kwargs)
            return JsonResponse(result)
            
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        except Exception as e:
            logger.error(f"R2 initialization failed for user {request.user.id}: {e}", exc_info=True)
            return JsonResponse({"error": "Internal server error initiating upload."}, status=500)


class UploadView(BaseR2UploadView):
    def get(self, request):
        context = {
            "page_title": "Upload a financial record.",
            "page_subtitle": "We’ll extract and organize the details automatically.",
            "api_url": reverse("documents:upload_document"),
            "redirect_url_template": "/records/add_record/__ID__",
        }
        return render(request, "documents/upload_file.html", context)

    def post(self, request):
        return self._handle_r2_upload(request)


class ViewDocument(LoginRequiredMixin, UpdateView):
    model = DocumentData
    form_class = DocumentUpdateForm
    template_name = "documents/view_document.html"
    context_object_name = "document"

    def get_template_names(self):
        if self.request.headers.get("HX-Target") == "search-results":
            return ["documents/partials/record_list_partial.html"]

        if self.request.headers.get("HX-Target") in ["document-form-container", "document-metadata-form"]:
            return ["documents/partials/document_form_partial.html"]
            
        return [self.template_name]

    def get_queryset(self):
        return DocumentData.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view_url"] = generate_read_presigned_url(self.object.filepath)
        context["records"] = self.search_records()
        return context

    def search_records(self):
        queryset = Record.objects.filter(user=self.request.user, is_active=True)
        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            queryset = queryset.smart_search(search_query)
        return queryset[0:20]

    def form_valid(self, form):
        if "associated_record" in self.request.POST:
            record_id = self.request.POST.get("associated_record", "").strip()
            if not record_id:
                form.instance.associated_record = None
            else:
                record = get_object_or_404(Record, pk=record_id, user=self.request.user)
                form.instance.associated_record = record
            
            form.save()

            if self.request.headers.get("HX-Request") == "true":
                return HttpResponse(status=204, headers={"HX-Refresh": "true"})
            return redirect('documents:view_document', pk=self.object.pk)

        form.save()
        if self.request.headers.get("HX-Request") == "true":
            return HttpResponse(status=204)
        return redirect('documents:view_document', pk=self.object.pk)

    def form_invalid(self, form):
        if self.request.headers.get("HX-Request") == "true":
            return self.render_to_response(self.get_context_data(form=form), status=422)
        return super().form_invalid(form)
        

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
                f"Failed to safely delete document {self.object.pk} for user {self.request.user.id}: {e}", 
                exc_info=True
            )
            messages.error(self.request, "Failed to complete deletion safely due to a system error.")
            
            if self.request.headers.get("HX-Request") == "true":
                return HttpResponse(status=204, headers={"HX-Refresh": "true"})
            
            return redirect(self.get_success_url())


class AddSupportDocuments(BaseR2UploadView):
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
        return self._handle_r2_upload(request, record_id=record_id)