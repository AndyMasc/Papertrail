from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

from .tasks import send_background_email


class QStashEmailBackend(BaseEmailBackend):
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
