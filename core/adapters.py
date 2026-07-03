from django.utils import timezone

from allauth.account.adapter import DefaultAccountAdapter


class MyLocalAllauthAdapter(DefaultAccountAdapter):
    def send_notification_mail(
        self,
        template_prefix,
        user,
        context=None,
        email=None,
    ):
        if context is None:
            context = {}

        context["timestamp"] = timezone.localtime()

        return super().send_notification_mail(
            template_prefix,
            user,
            context=context,
            email=email,
        )