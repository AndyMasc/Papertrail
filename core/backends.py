"""Custom email backend that sends messages asynchronously via QStash.

Replaces Django's synchronous SMTP backend with a task-queue approach so
that email delivery never blocks the request/response cycle.
"""

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

from .tasks import send_background_email


class QStashEmailBackend(BaseEmailBackend):
    """Email backend that enqueues each message as a QStash background task.

    Extracts HTML alternatives from the message and forwards everything to
    ``send_background_email`` for delivery through the Resend provider.
    """

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        sent_count = 0
        for message in email_messages:
            html_message = None
            if hasattr(message, "alternatives") and message.alternatives:
                for content, mimetype in message.alternatives:
                    if mimetype == "text/html":
                        html_message = content

            send_background_email.delay(
                subject=message.subject,
                message=message.body,
                from_email=message.from_email or settings.DEFAULT_FROM_EMAIL,
                recipient_list=message.to,
                html_message=html_message,
            )
            sent_count += 1

        return sent_count
