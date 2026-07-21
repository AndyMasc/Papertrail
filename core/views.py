import asyncio
import json
import logging
from datetime import datetime, time, timedelta
from typing import Any

from asgiref.sync import async_to_sync
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import DatabaseError, connection
from django.db.models import Sum
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.timezone import make_aware
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView, UpdateView
from webpush.models import PushInformation, SubscriptionInfo
from webpush.views import save_info

from documents.models import DocumentData, DocumentStatus
from records.models import MergeLog, Record

from .forms import UpdateUserSettingsForm
from .models import UserSettings

DASHBOARD_CACHE_TTL = 30

logger = logging.getLogger(__name__)


def index(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("core:dashboard")
    return render(request, "core/landing_page.html")


def privacy_policy(request: HttpRequest) -> HttpResponse:
    return render(request, "core/privacy_policy.html")


def health_check(request: HttpRequest) -> JsonResponse:  # noqa: ARG001
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        db_ok = False

    redis_ok = True
    try:
        from django.core.cache import cache

        cache.set("health_check_ping", "ok", timeout=5)
        if cache.get("health_check_ping") != "ok":
            raise ConnectionError("Cache ping failed")
    except Exception:
        redis_ok = False

    healthy = db_ok and redis_ok
    status = 200 if healthy else 503
    return JsonResponse(
        {
            "status": "healthy" if healthy else "unhealthy",
            "database": "connected" if db_ok else "disconnected",
            "cache": "connected" if redis_ok else "disconnected",
        },
        status=status,
    )


@csrf_exempt
@require_POST
def safe_webpush_save_info(request: HttpRequest) -> HttpResponse:
    try:
        post_data = json.loads(request.body.decode("utf-8"))
        endpoint = post_data.get("subscription", {}).get("endpoint")

        if endpoint:
            existing_subs = SubscriptionInfo.objects.filter(endpoint=endpoint)

            if existing_subs.exists():
                existing_subs.delete()
    except (json.JSONDecodeError, SubscriptionInfo.DoesNotExist):
        logger.warning("Failed to process webpush subscription info", exc_info=True)

    return save_info(request)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    @method_decorator(never_cache)
    def dispatch(self, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(*args, **kwargs)

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return async_to_sync(self._get_async)(request, *args, **kwargs)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return async_to_sync(self._context_data_async)(**kwargs)

    async def _get_async(self, request: HttpRequest) -> HttpResponse:
        user = await get_user_model().objects.select_related("settings").aget(pk=request.user.pk)
        webpush_enabled = await PushInformation.objects.filter(user=user).aexists()
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

        context = await self._context_data_async()
        return self.render_to_response(context)

    async def _context_data_async(self) -> dict[str, Any]:
        user = self.request.user
        cache_key = f"dashboard:{user.id}"
        cached = await cache.aget(cache_key)
        if cached is not None:
            return cached

        now = timezone.now()

        local_date = timezone.localdate(now)
        start_of_month = make_aware(
            datetime.combine(local_date.replace(day=1), time.min),
            timezone=timezone.get_current_timezone(),
        )
        expiring_cutoff = now + timedelta(days=30)

        all_user_records = Record.objects.for_user(user)
        active_records_qs = all_user_records.active()

        merge_count, monthly_expenses, orphaned_count, pending_ocr_count = await asyncio.gather(
            MergeLog.objects.filter(plaid_record__user=user, undone_at__isnull=True).acount(),
            all_user_records.filter(
                transaction_date__gte=start_of_month,
                transaction_date__lte=now,
                balance__isnull=False,
            ).aaggregate(total=Sum("balance")),
            DocumentData.objects.for_user(user).orphaned().acount(),
            DocumentData.objects.for_user(user)
            .filter(
                did_ocr=True,
                associated_record__isnull=True,
                status__in=[
                    DocumentStatus.UPLOADED,
                    DocumentStatus.PROCESSING,
                    DocumentStatus.COMPLETED,
                    DocumentStatus.ERROR,
                ],
            )
            .acount(),
        )

        recent_records = [
            r
            async for r in active_records_qs.order_by("-last_edited").only(
                "id",
                "title",
                "merchant",
                "balance",
                "expiry_date",
                "date_added",
                "last_edited",
            )[:5]
        ]

        expiring_soon = [
            r
            async for r in active_records_qs.filter(
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
        ]

        context = {
            "merged_records_count": merge_count,
            "records": recent_records,
            "expiring_soon": expiring_soon,
            "expiring_soon_count": len(expiring_soon),
            "monthly_expenses": monthly_expenses.get("total") or 0,
            "orphaned_document_count": orphaned_count,
            "pending_ocr_count": pending_ocr_count,
        }

        await cache.aset(cache_key, context, timeout=DASHBOARD_CACHE_TTL)
        return context


class ProfilePageView(LoginRequiredMixin, UpdateView):
    model = UserSettings
    template_name = "core/profile_page.html"
    context_object_name = "user_settings"
    form_class = UpdateUserSettingsForm
    success_url = reverse_lazy("core:profile_page")

    def get_object(self, queryset=None) -> UserSettings:  # noqa: ARG002
        user_settings, _ = UserSettings.objects.get_or_create(user=self.request.user)
        return user_settings

    def form_valid(self, form) -> HttpResponse:
        user_settings = form.save(commit=False)
        user_settings.user = self.request.user
        user_settings.save()

        messages.success(self.request, "Settings saved successfully.")

        if self.request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps(
                {"djangoMessages": [{"message": "Settings saved successfully.", "level": 25}]}
            )
            return response
        return super().form_valid(form)

    def form_invalid(self, form) -> HttpResponse:
        messages.error(self.request, "An unresolved error exists.")

        if self.request.headers.get("HX-Request"):
            response = render(
                self.request, "core/partials/user_settings_partial.html", {"form": form}
            )
            response.status_code = 422
            response["HX-Trigger"] = json.dumps(
                {"djangoMessages": [{"message": "An unresolved error exists.", "level": 40}]}
            )
            return response
        return super().form_invalid(form)
