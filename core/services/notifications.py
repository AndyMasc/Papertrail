"""Unified notification service supporting webpush, email, and database channels.

Provides helpers to build notification payloads, check per-user delivery
preferences, and dispatch notifications across one or more channels in a
single call.
"""

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.templatetags.static import static
from webpush import send_user_notification

from core.tasks import fire_single_webpush, send_background_email

logger = logging.getLogger(__name__)

User = get_user_model()


@dataclass
class NotificationContext:
    """Data transfer object bundling all inputs needed to send a multi-channel notification.

    Groups the common fields (user, subject, bodies, webpush payload) so that
    callers can construct a single object and pass it to ``send_multi_channel_notification``.
    """

    user: User
    subject: str
    text_body: str
    html_body: str
    webpush_payload: dict | None = None
    webpush_ttl: int = 1000


def build_site_context() -> dict:
    """Return site URL, domain, and name for use in notification templates.

    Falls back to a localhost default when ``SITE_URL`` is not configured.
    """
    site_url = getattr(settings, "SITE_URL", "http://localhost:8000")
    parsed_url = urlparse(site_url)
    site_domain = parsed_url.netloc

    try:
        current_site = Site.objects.get_current()
        site_info = {"domain": current_site.domain, "name": current_site.name}
    except Site.DoesNotExist:
        site_info = {"domain": site_domain, "name": "Papertrail"}

    return {
        "site_url": site_url,
        "site_domain": site_domain,
        "current_site": site_info,
    }


base_payload = {
    "icon": static("favicon-package/icon-512.png"),
    "url": settings.SITE_URL,
}


def build_expiry_webpush_payload(record_count: int) -> dict:
    """Build a webpush payload for the record expiry alert notification.

    Includes the app icon and a human-readable body that adapts to singular
    or plural record counts.
    """
    return {
        **base_payload,
        "head": "Record Expiry Alert",
        "body": f"You have {record_count} record{'s' if record_count > 1 else ''} expiring soon.",
    }


def build_expiry_email_context(
    user: User,
    records: list,
    remaining_count: int,
    total_records_count: int,
    auto_archive_msg: str,
    action_url: str,
) -> dict:
    """Assemble the template context for the expiry notification email.

    Merges site-wide context (URL, domain) with record-specific data so the
    email template can render a personalized summary.
    """
    site_context = build_site_context()

    return {
        "user": user,
        "records": records,
        "remaining_count": remaining_count,
        "total_records_count": total_records_count,
        "auto_archive_msg": auto_archive_msg,
        "action_url": action_url,
        **site_context,
    }


def send_push_notification(user: User, payload: dict, ttl: int = 1000) -> None:
    """Send a webpush notification synchronously, logging failures.

    This is a thin wrapper around django-webpush that centralizes error
    handling and logging for push delivery.
    """
    try:
        send_user_notification(user=user, payload=payload, ttl=ttl)
        logger.info(f"Dispatched webpush to {user.email}")
    except Exception as e:
        logger.error(f"Failed webpush delivery to {user.email}: {e}")


def send_email_notification(
    user: User,
    subject: str,
    text_body: str,
    html_body: str,
) -> None:
    """Enqueue an email notification as a QStash background task.

    Uses the user's email as the sole recipient. Delivery is asynchronous
    so this function returns immediately.
    """
    send_background_email.delay(
        subject=subject,
        message=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_body,
    )
    logger.info(f"Dispatched background email request for {user.email}")


def _user_can_receive_push(user: User) -> bool:
    """Check whether the user has push notifications enabled and at least one subscription.

    Returns False when the user has no settings, when push is disabled, or
    when the webpush module is unavailable.
    """
    if not hasattr(user, "settings"):
        return False
    if not user.settings.enable_push_notifications:
        return False
    try:
        from webpush.models import PushInformation

        return PushInformation.objects.filter(user=user).exists()
    except ImportError:
        logger.error("webpush module not available for push notification check")
        return False


def _user_can_receive_email(user: User) -> bool:
    """Check whether the user has email notifications enabled.

    Refreshes the settings from the database to avoid returning a stale value
    when the preference was recently toggled.
    """
    if not hasattr(user, "settings"):
        return False
    # Refresh from DB to avoid stale cached value
    user.settings.refresh_from_db()
    return user.settings.enable_email_notifications


def send_multi_channel_notification(
    user: User,
    subject: str,
    text_body: str,
    html_body: str,
    webpush_payload: dict | None = None,
    webpush_ttl: int = 1000,
    send_push: bool = True,
    send_email: bool = True,
    send_db: bool = False,
    db_message: str | None = None,
) -> None:
    """Dispatch a notification across push, email, and database channels.

    Each channel is independently gated: push is skipped if the user has no
    subscription or has push disabled, email is skipped if the user has email
    disabled, and the database record is only created when ``send_db`` is True
    and a message is provided.
    """
    if send_push and webpush_payload and _user_can_receive_push(user):
        fire_single_webpush.delay(user_id=user.id, payload=webpush_payload, ttl=webpush_ttl)

    if send_email and _user_can_receive_email(user):
        send_email_notification(
            user=user,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )

    if send_db and db_message:
        from core.models import Notification

        Notification.objects.create(recipient=user, message=db_message)
