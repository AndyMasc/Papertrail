from django.db.models import F
from django.utils import timezone
from django_qstash import stashed_task

from .models import Record


@stashed_task
def archive_expired_records() -> None:
    active_expired_records = Record.objects.filter(
        expiry_date__lt=timezone.now(),
        expiry_date__gt=F("date_added"),
        is_active=True,
        user__settings__auto_archive_expired_records=True,
    )
    active_expired_records.update(is_active=False)


@stashed_task
def delete_2month_archived_records() -> None:
    two_month_expired_records = Record.objects.filter(
        expiry_date__lt=timezone.now() - timezone.timedelta(days=60),
        expiry_date__gt=F("date_added"),
        is_active=False,
        user__settings__auto_delete_archived_records=True,
    )
    two_month_expired_records.delete()
