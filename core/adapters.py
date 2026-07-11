from django.utils import timezone
from allauth.account.adapter import DefaultAccountAdapter
from core.tasks import send_background_email


class QStashEmailAdapter(DefaultAccountAdapter):
    def send_notification_mail(self, template_prefix, user, context=None, email=None):
        if context is None:
            context = {}

        context["timestamp"] = timezone.localtime()

        return super().send_notification_mail(
            template_prefix,
            user,
            context=context,
            email=email,
        )

    def send_mail(self, template_prefix, email, context):  # Intercepts allauth's email creation logic and defers sending via django-qstash instead of sending it synchronously.
        msg = self.render_mail(template_prefix, email, context)
        
        # Extract plain text content
        message = msg.body
        
        # Check for HTML content alternatives if present
        html_message = None
        if hasattr(msg, "alternatives") and msg.alternatives:
            for content, mimetype in msg.alternatives:
                if mimetype == "text/html":
                    html_message = content
                    break

        # Queue the email using QStash
        send_background_email.delay(
            subject=msg.subject,
            message=message,
            from_email=msg.from_email,
            recipient_list=msg.to,
            html_message=html_message
        )