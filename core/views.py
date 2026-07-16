from datetime import datetime, time, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
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


def health_check(request):
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        db_ok = False

    status = 200 if db_ok else 503
    return JsonResponse(
        {
            "status": "healthy" if db_ok else "unhealthy",
            "database": "connected" if db_ok else "disconnected",
        },
        status=status,
    )


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

        # Base active queryset for this specific user
        active_records_qs = Record.objects.for_user(user).active()

        # Calculate high-level aggregates
        stats = active_records_qs.aggregate(
            active_count=Count("id"),
            monthly_expenses=Sum(
                "balance",
                filter=Q(date_added__gte=start_of_month, date_added__lte=now),
                default=0,
            ),
        )

        # Pull the primary working slice ordered by most recent activity
        recent_and_expiring = list(
            active_records_qs.filter(
                Q(expiry_date__gte=now, expiry_date__lte=expiring_cutoff)
                | Q(last_edited__isnull=False)
            )
            .order_by("-last_edited")
            .select_related()
            .only(
                "id",
                "title",
                "merchant",
                "balance",
                "expiry_date",
                "date_added",
                "last_edited",
            )[:9]
        )

        # 1. Filter out the subset that specifically expires within 30 days
        expiring_soon = [
            r
            for r in recent_and_expiring
            if r.expiry_date and now.date() <= r.expiry_date <= expiring_cutoff.date()
        ][:4]

        # 2. FIXED: Grab the top 5 most recently edited/added records unconditionally
        # so newly made records show up here instantly even if they expire soon.
        recent_records = recent_and_expiring[:5]

        # Hydrate the UI context payload
        context["active_records_count"] = stats["active_count"]
        context["records"] = recent_records
        context["expiring_soon"] = expiring_soon
        context["expiring_soon_count"] = len(expiring_soon)
        context["monthly_expenses"] = stats["monthly_expenses"] or 0
        context["orphaned_document_count"] = (
            DocumentData.objects.for_user(user).orphaned().count()
        )

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
            response = render(
                self.request, "core/partials/user_settings_partial.html", {"form": form}
            )
            response.status_code = 422
            return response
        return super().form_invalid(form)
