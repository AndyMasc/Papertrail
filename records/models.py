import calendar
import datetime
import re
from decimal import Decimal, InvalidOperation
from functools import reduce
from operator import or_

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q

_MONTH_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_RELATIVE_DAYS = frozenset({"today", "yesterday", "tomorrow"})
_YEAR_RE = re.compile(r"^\d{4}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TEXT_SEARCH_FIELDS = ("title", "merchant", "products", "notes")
_DATE_FIELDS = ("transaction_date", "expiry_date", "date_added")


def _month_range(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 1), datetime.date(year, month, last_day)


class RecordQuerySet(models.QuerySet):
    def smart_search(self, search_query: str):
        if not (search_query := search_query.strip()):
            return self

        lower = search_query.lower()

        conditions = reduce(
            or_,
            (
                Q(**{f"{field}__icontains": search_query})
                for field in _TEXT_SEARCH_FIELDS
            ),
        )

        from .models import Record

        matching_choices = [
            k
            for k, v in Record.RecordTypes.choices
            if search_query.lower() in k.lower() or search_query.lower() in v.lower()
        ]
        if matching_choices:
            conditions |= Q(record_type__in=matching_choices)

        clean_num = "".join(c for c in search_query if c.isdigit() or c == ".")
        if clean_num and clean_num.replace(".", "", 1).isdigit():
            try:
                val = Decimal(clean_num)
                conditions |= Q(balance__gte=val, balance__lt=val + 1)
            except (InvalidOperation, ValueError, OverflowError):
                pass

        start: datetime.date | None = None
        end: datetime.date | None = None

        if lower in _RELATIVE_DAYS:
            delta = {"today": 0, "yesterday": -1, "tomorrow": 1}[lower]
            start = end = datetime.date.today() + datetime.timedelta(days=delta)

        elif _YEAR_RE.match(search_query):
            year = int(search_query)
            start, end = datetime.date(year, 1, 1), datetime.date(year, 12, 31)

        elif lower in _MONTH_MAP:
            start, end = _month_range(datetime.date.today().year, _MONTH_MAP[lower])

        elif _ISO_DATE_RE.match(search_query):
            try:
                start = end = datetime.date.fromisoformat(search_query)
            except ValueError:
                pass

        # Apply date filters explicitly across indexed range properties
        if start is not None and end is not None:
            conditions |= reduce(
                or_,
                (Q(**{f"{f}__range": (start, end)}) for f in _DATE_FIELDS),
            )

        return self.filter(conditions)

    def _build_text_conditions(self, query: str) -> Q:
        return reduce(
            or_,
            (Q(**{f"{field}__icontains": query}) for field in _TEXT_SEARCH_FIELDS),
        )

    def _append_numeric_condition(self, conditions: Q, query: str) -> None:
        clean = "".join(c for c in query if c.isdigit() or c == ".")
        if not clean or not clean.replace(".", "", 1).isdigit():
            return
        try:
            val = Decimal(clean)
            conditions |= Q(balance__gte=val, balance__lt=val + 1)
        except (ValueError, OverflowError, InvalidOperation):
            pass

    def _append_date_range_conditions(self, conditions: Q, query: str) -> None:
        lower = query.lower()
        today = datetime.date.today()
        start: datetime.date | None = None
        end: datetime.date | None = None

        if lower in _RELATIVE_DAYS:
            delta = {"today": 0, "yesterday": -1, "tomorrow": 1}[lower]
            start = end = today + datetime.timedelta(days=delta)

        elif _YEAR_RE.match(query):
            year = int(query)
            start, end = datetime.date(year, 1, 1), datetime.date(year, 12, 31)

        elif lower in _MONTH_MAP:
            start, end = _month_range(today.year, _MONTH_MAP[lower])

        elif _ISO_DATE_RE.match(query):
            try:
                start = end = datetime.date.fromisoformat(query)
            except ValueError:
                return

        if start is not None and end is not None:
            conditions |= reduce(
                or_,
                (Q(**{f"{f}__range": (start, end)}) for f in _DATE_FIELDS),
            )


class Record(models.Model):
    class RecordTypes(models.TextChoices):
        EXPENSE_RECEIPT = "expense_receipt", "Expense Receipt"
        VOUCHER = "voucher", "Voucher"
        WARRANTY_CERTIFICATE = "warranty_certificate", "Warranty Certificate"
        VENDOR_INVOICE = "vendor_invoice", "Vendor Invoice"
        CUSTOMER_INVOICE = "customer_invoice", "Customer Invoice"
        LOAN_DOCUMENT = "loan_document", "Loan Document"
        CREDIT_CARD_STATEMENT = "credit_card_statement", "Credit Card Statement"
        BANK_STATEMENT = "bank_statement", "Bank Statement"
        PURCHASE_ORDER = "purchase_order", "Purchase Order"
        PAYSLIP = "payslip", "Payslip"
        TAX_DOCUMENT = "tax_document", "Tax Document"
        SERVICE_CONTRACT = "service_contract", "Service Contract"
        LEASE_AGREEMENT = "lease_agreement", "Lease Agreement"
        INSURANCE_POLICY = "insurance_policy", "Insurance Policy"
        OTHER = "other", "Other"

    COLOR_MAP = {
        RecordTypes.EXPENSE_RECEIPT.value: "bg-emerald-500/10 text-emerald-700 border border-emerald-500/20 backdrop-blur-md dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/30",
        RecordTypes.VOUCHER.value: "bg-amber-500/10 text-amber-700 border border-amber-500/20 backdrop-blur-md dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/30",
        RecordTypes.WARRANTY_CERTIFICATE.value: "bg-green-500/10 text-green-700 border border-green-500/20 backdrop-blur-md dark:bg-green-500/10 dark:text-green-400 dark:border-green-500/30",
        RecordTypes.VENDOR_INVOICE.value: "bg-blue-500/10 text-blue-700 border border-blue-500/20 backdrop-blur-md dark:bg-blue-500/10 dark:text-blue-400 dark:border-blue-500/30",
        RecordTypes.CUSTOMER_INVOICE.value: "bg-indigo-500/10 text-indigo-700 border border-indigo-500/20 backdrop-blur-md dark:bg-indigo-500/10 dark:text-indigo-400 dark:border-indigo-500/30",
        RecordTypes.LOAN_DOCUMENT.value: "bg-red-500/10 text-red-700 border border-red-500/20 backdrop-blur-md dark:bg-red-500/10 dark:text-red-400 dark:border-red-500/30",
        RecordTypes.CREDIT_CARD_STATEMENT.value: "bg-sky-500/10 text-sky-700 border border-sky-500/20 backdrop-blur-md dark:bg-sky-500/10 dark:text-sky-400 dark:border-sky-500/30",
        RecordTypes.BANK_STATEMENT.value: "bg-cyan-500/10 text-cyan-700 border border-cyan-500/20 backdrop-blur-md dark:bg-cyan-500/10 dark:text-cyan-400 dark:border-cyan-500/30",
        RecordTypes.PURCHASE_ORDER.value: "bg-violet-500/10 text-violet-700 border border-violet-500/20 backdrop-blur-md dark:bg-violet-500/10 dark:text-violet-400 dark:border-violet-500/30",
        RecordTypes.PAYSLIP.value: "bg-lime-500/10 text-lime-700 border border-lime-500/20 backdrop-blur-md dark:bg-lime-500/10 dark:text-lime-400 dark:border-lime-500/30",
        RecordTypes.TAX_DOCUMENT.value: "bg-purple-500/10 text-purple-700 border border-purple-500/20 backdrop-blur-md dark:bg-purple-500/10 dark:text-purple-400 dark:border-purple-500/30",
        RecordTypes.SERVICE_CONTRACT.value: "bg-teal-500/10 text-teal-700 border border-teal-500/20 backdrop-blur-md dark:bg-teal-500/10 dark:text-teal-400 dark:border-teal-500/30",
        RecordTypes.LEASE_AGREEMENT.value: "bg-orange-500/10 text-orange-700 border border-orange-500/20 backdrop-blur-md dark:bg-orange-500/10 dark:text-orange-400 dark:border-orange-500/30",
        RecordTypes.INSURANCE_POLICY.value: "bg-rose-500/10 text-rose-700 border border-rose-500/20 backdrop-blur-md dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/30",
        RecordTypes.OTHER.value: "bg-slate-500/10 text-slate-700 border border-slate-500/20 backdrop-blur-md dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30",
    }

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date_added = models.DateField(auto_now_add=True)
    last_edited = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    title = models.CharField(max_length=255)
    merchant = models.CharField(max_length=255, blank=True, null=True)
    balance = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True
    )
    products = models.TextField()
    transaction_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    record_type = models.CharField(
        max_length=30, choices=RecordTypes.choices, default=RecordTypes.EXPENSE_RECEIPT
    )

    objects = RecordQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["user", "-last_edited"]),
            models.Index(fields=["user", "record_type"]),
            models.Index(fields=["expiry_date"]),
            models.Index(fields=["transaction_date"]),
            models.Index(
                fields=["user", "is_active", "-last_edited"],
                name="idx_record_list_cover",
            ),
            models.Index(
                fields=["user", "is_active", "record_type"],
                name="idx_record_type_filter",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def badge_classes(self):
        return self.COLOR_MAP.get(
            self.record_type, self.COLOR_MAP[self.RecordTypes.OTHER.value]
        )
