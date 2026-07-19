import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django_qstash import shared_task

from core.models import Notification
from core.services.notifications import (
    build_expiry_email_context,
    build_expiry_webpush_payload,
    build_site_context,
    send_multi_channel_notification,
)

from .models import Record

logger = logging.getLogger(__name__)

User = get_user_model()


@shared_task
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


@shared_task
def delete_2month_archived_records() -> None:
    from documents.models import DocumentData
    from documents.tasks import delete_document as delete_s3_object

    two_months_ago = timezone.now() - timedelta(days=60)
    two_month_expired_records = Record.objects.filter(
        last_edited__lt=two_months_ago,
        expiry_date__gte=F("date_added"),
        is_active=False,
        user__settings__auto_delete_archived_records=True,
    )

    document_paths = list(
        DocumentData.objects.filter(associated_record__in=two_month_expired_records).values_list(
            "filepath", flat=True
        )
    )

    deleted_count, _ = two_month_expired_records.delete()
    if deleted_count:
        logger.info("Deleted %d archived records older than 60 days.", deleted_count)

    for path in document_paths:
        if path:
            delete_s3_object.delay(path)


@shared_task
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
                user__settings__expiring_notifications_advance_time="30",
                expiry_date__lte=today + timedelta(days=30),
            )
            | Q(user__settings__isnull=True, expiry_date__lte=today + timedelta(days=7))
        )
        .select_related("user__settings")
    )

    notifications_to_create: list = []
    user_records_map: dict[int, list] = {}
    user_object_map: dict[int, object] = {}
    user_settings_cache = {}

    for record in expiring_records:
        user = record.user
        notifications_to_create.append(
            Notification(
                recipient=user,
                message=f"Your record '{record.title}' is expiring on {record.expiry_date}.",
            )
        )

        user_records_map.setdefault(user.id, []).append(record)
        if user.id not in user_object_map:
            user_object_map[user.id] = user

        if user.id not in user_settings_cache:
            user_settings_cache[user.id] = getattr(user, "settings", None)

    if not notifications_to_create:
        return

    Notification.objects.bulk_create(notifications_to_create)

    record_ids = [r.id for r in expiring_records]
    Record.objects.filter(id__in=record_ids).update(expiry_notification_sent=True)
    logger.info("Created %d DB notifications.", len(notifications_to_create))

    site_context = build_site_context()
    site_url = site_context["site_url"]

    for user_id, records in user_records_map.items():
        user = user_object_map[user_id]

        webpush_payload = build_expiry_webpush_payload(len(records))

        user_settings = user_settings_cache.get(user_id)
        auto_archive_msg = (
            "Since you have enabled auto-archiving, your records will be automatically archived once the expiry passes."
            if user_settings and getattr(user_settings, "auto_archive_expired_records", False)
            else ""
        )

        action_url = f"{site_url.rstrip('/')}{reverse('core:dashboard')}"
        MAX_DISPLAY_RECORDS = 5
        total_records_count = len(records)

        display_records = records[:MAX_DISPLAY_RECORDS]
        remaining_count = max(0, total_records_count - MAX_DISPLAY_RECORDS)

        context = build_expiry_email_context(
            user=user,
            records=display_records,
            remaining_count=remaining_count,
            total_records_count=total_records_count,
            auto_archive_msg=auto_archive_msg,
            action_url=action_url,
        )

        send_multi_channel_notification(
            user=user,
            subject="Expiring Records on Papertrail",
            text_body=render_to_string("notifications/expiring_record_email.txt", context),
            html_body=render_to_string("notifications/expiring_record_email.html", context),
            webpush_payload=webpush_payload,
            send_db=True,
            db_message=f"Your record '{', '.join(r.title for r in records[:3])}' is expiring soon.",
        )

    logger.info("Successfully scheduled notices for %d unique users.", len(user_records_map))
