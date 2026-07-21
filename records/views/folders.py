import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView

from ..forms import FolderForm
from ..models import Folder

logger = logging.getLogger(__name__)


class FolderListView(LoginRequiredMixin, ListView):
    model = Folder
    template_name = "records/folders.html"
    context_object_name = "folders"
    ordering = ["-created_at"]
    paginate_by = 12

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["records/partials/folder_list_partial.html"]
        return [self.template_name]

    def get_queryset(self):
        qs = Folder.objects.filter(user=self.request.user)
        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            qs = qs.filter(name__icontains=search_query)
        return qs.annotate(
            active_records_count=Count("records", filter=Q(records__is_active=True))
        ).order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["unfiled_count"] = self.request.user.records.filter(
            folder__isnull=True, is_active=True
        ).count()
        return context


class CreateFolder(LoginRequiredMixin, CreateView):
    model = Folder
    form_class = FolderForm
    template_name = "records/partials/create_folder_modal.html"

    def form_valid(self, form):
        form.instance.user = self.request.user
        self.object = form.save()
        if self.request.headers.get("HX-Request"):
            folders = Folder.objects.filter(user=self.request.user).annotate(
                active_records_count=Count("records", filter=Q(records__is_active=True))
            )
            unfiled_count = self.request.user.records.filter(
                folder__isnull=True, is_active=True
            ).count()
            response = render(
                self.request,
                "records/partials/folder_list_partial.html",
                {"folders": folders, "unfiled_count": unfiled_count, "page_obj": None},
            )
            response["HX-Trigger"] = json.dumps({"closeModal": True})
            return response
        return super().form_valid(form)

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if self.request.headers.get("HX-Request"):
            response.status_code = 422
        return response


class FolderUpdateView(LoginRequiredMixin, UpdateView):
    model = Folder
    form_class = FolderForm
    template_name = "records/partials/edit_folder_inline.html"
    pk_url_kwarg = "folder_id"

    def get_queryset(self):
        return Folder.objects.filter(user=self.request.user).annotate(
            active_records_count=Count("records", filter=Q(records__is_active=True))
        )

    def form_valid(self, form):
        self.object = form.save()
        if self.request.headers.get("HX-Request"):
            return render(
                self.request,
                "records/partials/folder_item_partial.html",
                {"folder": self.object},
            )
        return super().form_valid(form)

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if self.request.headers.get("HX-Request"):
            response.status_code = 422
        return response


class FolderDeleteView(LoginRequiredMixin, DeleteView):
    model = Folder
    pk_url_kwarg = "folder_id"
    success_url = reverse_lazy("records:view_folders")

    def get_queryset(self):
        return Folder.objects.filter(user=self.request.user)

    def delete(self, request, *_, **__):
        folder = self.get_object()
        folder.records.update(folder=None)
        folder.delete()
        if request.headers.get("HX-Request"):
            folders = Folder.objects.filter(user=self.request.user).annotate(
                active_records_count=Count("records", filter=Q(records__is_active=True))
            )
            unfiled_count = request.user.records.filter(folder__isnull=True, is_active=True).count()
            return render(
                request,
                "records/partials/folder_list_partial.html",
                {"folders": folders, "unfiled_count": unfiled_count, "page_obj": None},
            )
        messages.info(request, "Folder deleted. Records unfiled.")
        return redirect(self.success_url)
