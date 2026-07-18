from core.services.notifications import (
    NotificationContext,
    build_expiry_email_context,
    build_expiry_webpush_payload,
    build_site_context,
    send_email_notification,
    send_multi_channel_notification,
    send_push_notification,
)

# Backward compatibility aliases
send_push_notification_sync = send_push_notification
send_email_notification_sync = send_email_notification

__all__ = [
    "send_push_notification",
    "send_email_notification",
    "send_push_notification_sync",
    "send_email_notification_sync",
    "send_multi_channel_notification",
    "build_site_context",
    "build_expiry_email_context",
    "build_expiry_webpush_payload",
    "NotificationContext",
]
