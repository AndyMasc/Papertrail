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
    user: User
    subject: str
    text_body: str
    html_body: str
    webpush_payload: dict | None = None
    webpush_ttl: int = 1000


def build_site_context() -> dict:
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
    send_background_email.delay(
        subject=subject,
        message=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_body,
    )
    logger.info(f"Dispatched background email request for {user.email}")


def _user_can_receive_push(user: User) -> bool:
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
    """Check if user has email notifications enabled."""
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
