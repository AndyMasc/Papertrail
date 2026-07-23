from django.contrib.auth.models import User
from django.db.models import QuerySet

from records.models import Record

from .resources import RecordResource


def export_to_excel(user: User) -> bytes:
    queryset = Record.objects.filter(user=user)
    dataset = RecordResource().export(queryset=queryset)
    return dataset.xlsx


def export_records_to_excel(queryset: QuerySet[Record]) -> bytes:
    """Export a specific queryset of records to xlsx."""
    dataset = RecordResource().export(queryset=queryset)
    return dataset.xlsx
