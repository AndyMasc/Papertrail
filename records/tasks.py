import logging
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django_qstash import stashed_task

from core.models import Notification
from core.tasks import send_background_email
from webpush import send_user_notification
from .models import Record

logger = logging.getLogger(__name__)


@stashed_task
def archive_expired_records() -> None:
    today = timezone.now().date()

    active_expired_records = Record.objects.filter(
        expiry_date__lt=today,
        expiry_date__gte=F("date_added"),
        is_active=True,
        user__settings__auto_archive_expired_records=True,
    )
    count = active_expired_records.update(is_active=False)
    if count:
        logger.info("Archived %d expired records.", count)


@stashed_task
def delete_2month_archived_records() -> None:
    two_months_ago = timezone.now() - timedelta(days=60)

    two_month_expired_records = Record.objects.filter(
        last_edited__lt=two_months_ago,
        expiry_date__gte=F("date_added"),
        is_active=False,
        user__settings__auto_delete_archived_records=True,
    )
    deleted_count, _ = two_month_expired_records.delete()
    if deleted_count:
        logger.info("Deleted %d archived records older than 60 days.", deleted_count)


@stashed_task
def send_expiry_notifications() -> None:
    today = timezone.now().date()

    expiring_records = (
        Record.objects.filter(
            is_active=True,
            expiry_notification_sent=False,
            expiry_date__isnull=False,
            expiry_date__gte=F("date_added"),
        )
        .filter(
            Q(
                user__settings__expiring_notifications_advance_time="1",
                expiry_date__lte=today + timedelta(days=1),
            )
            | Q(
                user__settings__expiring_notifications_advance_time="3",
                expiry_date__lte=today + timedelta(days=3),
            )
            | Q(
                user__settings__expiring_notifications_advance_time="7",
                expiry_date__lte=today + timedelta(days=7),
            )
            | Q(
                user__settings__expiring_notifications_advance_time="14",
                expiry_date__lte=today + timedelta(days=14),
            )
            | Q(
                user__settings__expiring_notifications_advance_time="30",
                expiry_date__lte=today + timedelta(days=30),
            )
            | Q(user__settings__isnull=True, expiry_date__lte=today + timedelta(days=7))
        )
        .select_related("user__settings")
    )

    notifications_to_create = []
    user_records_map = {}
    user_settings_cache = {}

    for record in expiring_records:
        user = record.user
        notifications_to_create.append(
            Notification(
                recipient=user,
                message=f"Your record '{record.title}' is expiring on {record.expiry_date}.",
            )
        )
        user_records_map.setdefault(user, []).append(record)

        if user not in user_settings_cache:
            user_settings_cache[user] = getattr(user, "settings", None)

    if notifications_to_create:
        Notification.objects.bulk_create(notifications_to_create)

        record_ids = [r.id for r in expiring_records]
        Record.objects.filter(id__in=record_ids).update(expiry_notification_sent=True)

        logger.info("Created %d DB notifications.", len(notifications_to_create))

        site_url = getattr(settings, "SITE_URL", "http://localhost:8000")
        parsed_url = urlparse(site_url)
        site_domain_plain = parsed_url.netloc

        for user, records in user_records_map.items():
            # Send push notifications
            payload = {
                "head": "Record Expiry Alert",
                "body": f"You have {len(records)} records expiring soon.",
                # "icon": "https://yourdomain.com/static/icon.png",
                # "url": f"{site_url.rstrip('/')}{reverse('core:dashboard')}"
            }
            try:
                send_user_notification(user=user, payload=payload, ttl=1000)
                logger.info(f"Dispatched push notification to {user.email}")
            except Exception as e:
                logger.error(f"Failed to send push to {user.email}: {e}")

            # Send email notifications
            user_settings = user_settings_cache.get(user)

            if user_settings and getattr(
                user_settings, "auto_archive_expired_records", False
            ):
                auto_archive_msg = (
                    "Since you have enabled auto-archiving, your records will be "
                    "automatically archived once the expiry passes."
                )
            else:
                auto_archive_msg = ""

            action_url = f"{site_url.rstrip('/')}{reverse('core:dashboard')}"

            MAX_DISPLAY_RECORDS = 5
            total_records_count = len(records)

            if total_records_count > MAX_DISPLAY_RECORDS:
                display_records = records[:MAX_DISPLAY_RECORDS]
                remaining_count = total_records_count - MAX_DISPLAY_RECORDS
            else:
                display_records = records
                remaining_count = 0

            context_payload = {
                "user": user,
                "records": display_records,
                "remaining_count": remaining_count,
                "total_records_count": total_records_count,
                "auto_archive_msg": auto_archive_msg,
                "action_url": action_url,
                "site_url_base": site_url,
                "site_domain_plain": site_domain_plain,
            }

            text_body = render_to_string(
                "notifications/expiring_record_email.txt", context_payload
            )
            html_body = render_to_string(
                "notifications/expiring_record_email.html", context_payload
            )

            send_background_email.delay(
                subject="Expiring Records on Papertrail",
                message=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_body,
            )

        logger.info("Dispatched background emails to %d users.", len(user_records_map))
