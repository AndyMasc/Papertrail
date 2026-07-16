import logging
from django.conf import settings
from webpush import send_user_notification
from core.tasks import send_background_email

logger = logging.getLogger(__name__)

def send_push_notification_sync(user, payload: dict, ttl: int = 1000) -> None:
    try:
        send_user_notification(user=user, payload=payload, ttl=ttl)
        logger.info(f"Dispatched webpush to {user.email}")
    except Exception as e:
        logger.error(f"Failed webpush delivery to {user.email}: {e}")


def send_email_notification_sync(user, subject: str, text_body: str, html_body: str) -> None:
    send_background_email.delay(
        subject=subject,
        message=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_body,
    )
    logger.info(f"Dispatched background email request for {user.email}")