from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.timezone import make_aware
from django.views.generic import TemplateView, UpdateView
from django.contrib import messages
from webpush.models import PushInformation

from documents.models import DocumentData
from records.models import Record

from .forms import UpdateUserSettingsForm
from .models import UserSettings

import json
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from webpush.views import save_info
from webpush.models import SubscriptionInfo


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


@csrf_exempt
@require_POST
def safe_webpush_save_info(request):
    try:
        post_data = json.loads(request.body.decode("utf-8"))
        endpoint = post_data.get("subscription", {}).get("endpoint")

        if endpoint:
            existing_subs = SubscriptionInfo.objects.filter(endpoint=endpoint)

            if existing_subs.exists():
                existing_subs.delete()
    except Exception:
        pass

    return save_info(request)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get(self, request, *args, **kwargs):
        user = get_user_model().objects.select_related("settings").get(pk=request.user.pk)
        webpush_enabled = PushInformation.objects.filter(user=user).exists()
        if not webpush_enabled and user.settings.enable_push_notifications:
            messages.warning(
                self.request,
                "Subscribe to push messages in settings to recieve push notifications.",
            )
        elif webpush_enabled and not user.settings.enable_push_notifications:
            messages.warning(
                self.request,
                "Enable push messages in settings to recieve push notifications.",
            )

        return super().get(request, *args, **kwargs)

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

        all_user_records = Record.objects.for_user(user)
        active_records_qs = all_user_records.active()

        active_count = active_records_qs.count()

        monthly_expenses = (
            all_user_records.filter(
                transaction_date__gte=start_of_month,
                transaction_date__lte=now,
                balance__isnull=False,
            ).aggregate(total=Sum("balance"))["total"]
            or 0
        )

        recent_records = list(
            active_records_qs.order_by("-last_edited").only(
                "id",
                "title",
                "merchant",
                "balance",
                "expiry_date",
                "date_added",
                "last_edited",
            )[:5]
        )

        expiring_soon = list(
            active_records_qs.filter(
                expiry_date__gte=now.date(), expiry_date__lte=expiring_cutoff.date()
            )
            .order_by("expiry_date")
            .only(
                "id",
                "title",
                "merchant",
                "balance",
                "expiry_date",
                "date_added",
                "last_edited",
            )
        )

        # Context assignment
        context["active_records_count"] = active_count
        context["records"] = recent_records
        context["expiring_soon"] = expiring_soon
        context["expiring_soon_count"] = len(expiring_soon)
        context["monthly_expenses"] = monthly_expenses
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

        messages.success(self.request, "Settings saved successfully.")

        if self.request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps(
                {
                    "djangoMessages": [
                        {"message": "Settings saved successfully.", "level": 25}
                    ]
                }
            )
            return response
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "An unresolved error exists.")

        if self.request.headers.get("HX-Request"):
            response = render(
                self.request, "core/partials/user_settings_partial.html", {"form": form}
            )
            response.status_code = 422
            response["HX-Trigger"] = json.dumps(
                {
                    "djangoMessages": [
                        {"message": "An unresolved error exists.", "level": 40}
                    ]
                }
            )
            return response
        return super().form_invalid(form)
