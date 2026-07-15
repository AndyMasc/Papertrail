from datetime import datetime, time, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.timezone import make_aware
from django.views.generic import TemplateView, UpdateView

from documents.models import DocumentData
from records.models import Record

from .forms import UpdateUserSettingsForm
from .models import UserSettings


def index(request):
    return render(request, "core/landing_page.html")


def privacy_policy(request):
    return render(request, "core/privacy_policy.html")


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        now = timezone.now()

        local_date = timezone.localdate(now)
        start_of_month = make_aware(
            datetime.combine(local_date.replace(day=1), time.min),
            timezone=timezone.get_current_timezone(),
        )
        expiring_cutoff = now + timedelta(days=30)

        active_records_qs = Record.objects.filter(user=user, is_active=True)

        stats = active_records_qs.aggregate(
            active_count=Count("id"),
            monthly_expenses=Sum(
                "balance",
                filter=Q(date_added__gte=start_of_month, date_added__lte=now),
                default=0,
            ),
        )

        expiring_soon = list(
            active_records_qs.filter(
                expiry_date__gte=now,
                expiry_date__lte=expiring_cutoff,
            ).order_by("-date_added")[:4]
        )

        context["active_records_count"] = stats["active_count"]
        context["records"] = list(active_records_qs.order_by("-last_edited")[:5])

        context["expiring_soon"] = expiring_soon
        context["expiring_soon_count"] = len(expiring_soon)

        context["expiring_soon"] = expiring_soon
        context["expiring_soon_count"] = len(expiring_soon)
        context["monthly_expenses"] = stats["monthly_expenses"] or 0

        context["orphaned_document_count"] = DocumentData.objects.filter(
            user=user,
            associated_record__isnull=True,
        ).count()

        return context


class ProfilePageView(LoginRequiredMixin, UpdateView):
    model = UserSettings
    template_name = "core/profile_page.html"
    context_object_name = "user_settings"
    form_class = UpdateUserSettingsForm
    success_url = reverse_lazy("core:profile_page")

    def get_object(self, queryset=None):
        user_settings, _ = UserSettings.objects.get_or_create(user=self.request.user)
        return user_settings

    def form_valid(self, form):
        user_settings = form.save(commit=False)
        user_settings.user = self.request.user
        user_settings.save()

        if self.request.headers.get("HX-Request"):
            return render(
                self.request,
                "core/partials/user_settings_partial.html",
                {"form": form, "success": True},
            )
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get("HX-Request"):
            return render(
                self.request, "core/partials/user_settings_partial.html", {"form": form}
            )
        return super().form_invalid(form)
