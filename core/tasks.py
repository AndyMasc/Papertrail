from django_qstash import stashed_task
from django.core.mail import send_mail

@stashed_task
def send_background_email(subject, message, from_email, recipient_list, html_message=None): # Executes inside the QStash webhook handler to deliver the queued email.
    send_mail(
        subject=subject,
        message=message,
        from_email=from_email,
        recipient_list=recipient_list,
        html_message=html_message,
        fail_silently=False
    )