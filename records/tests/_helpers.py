from datetime import date
from decimal import Decimal

from django.http import HttpRequest

from records.models import Record


def make_filter_request(user):
    req = HttpRequest()
    req.user = user
    return req


def make_plaid_record(user, title: str, **overrides) -> Record:
    defaults = {
        "merchant": title,
        "balance": Decimal("100.00"),
        "transaction_date": date(2024, 6, 15),
        "record_type": Record.RecordTypes.EXPENSE_RECEIPT,
        "plaid_transaction_id": title.lower().replace(" ", "_"),
    }
    defaults.update(overrides)
    return Record.objects.create(user=user, title=title, **defaults)


def make_doc_record(user, title: str, **overrides) -> Record:
    defaults = {
        "merchant": title,
        "balance": Decimal("100.00"),
        "transaction_date": date(2024, 6, 15),
        "record_type": Record.RecordTypes.FINANCIAL_DOCUMENT,
        "products": "Test Product",
        "notes": f"Note for {title}",
    }
    defaults.update(overrides)
    return Record.objects.create(user=user, title=title, **defaults)
