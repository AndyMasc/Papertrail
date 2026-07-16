from django.core.mail import EmailMultiAlternatives, get_connection
from django_qstash import stashed_task


@stashed_task
def send_background_email(
    subject, message, from_email, recipient_list, html_message=None
):
    resend_connection = get_connection(backend="anymail.backends.resend.EmailBackend")

    email = EmailMultiAlternatives(
        subject=subject,
        body=message,
        from_email=from_email,
        to=recipient_list,
        connection=resend_connection,
    )

    if html_message:
        email.attach_alternative(html_message, "text/html")

    email.send()
