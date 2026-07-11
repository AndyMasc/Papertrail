import dateparser
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q

class RecordQuerySet(models.QuerySet):
    MONTH_SHORTCUTS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

    def smart_search(self, search_query):
        search_query = search_query.strip()
        if not search_query:
            return self

        conditions = [
            Q(title__icontains=search_query),
            Q(merchant__icontains=search_query),
            Q(products__icontains=search_query),
            Q(notes__icontains=search_query),
            Q(record_type__icontains=search_query),
        ]

        clean_numeric = ''.join(c for c in search_query if c.isdigit() or c == '.')
        if clean_numeric and clean_numeric.replace('.', '', 1).isdigit():
            conditions.append(
                Q(balance__gte=float(clean_numeric)) & 
                Q(balance__lte=float(clean_numeric) + 0.99)
            )

        parsed_date = dateparser.parse(
            search_query,
            settings={"PREFER_DATES_FROM": "past", "STRICT_PARSING": False},
        )

        if parsed_date:
            lower_query = search_query.lower()

            if search_query.isdigit() and len(search_query) == 4:
                conditions.extend([
                    Q(transaction_date__year=parsed_date.year),
                    Q(expiry_date__year=parsed_date.year),
                    Q(date_added__year=parsed_date.year),
                ])
            elif search_query.isalpha() and any(m in lower_query for m in self.MONTH_SHORTCUTS):
                conditions.extend([
                    Q(transaction_date__month=parsed_date.month),
                    Q(expiry_date__month=parsed_date.month),
                    Q(date_added__month=parsed_date.month),
                ])
            else:
                conditions.extend([
                    Q(transaction_date=parsed_date.date()),
                    Q(expiry_date=parsed_date.date()),
                    Q(date_added=parsed_date.date()),
                ])
                if not any(w in lower_query for w in ["today", "yesterday", "tomorrow"]):
                    conditions.extend([
                        Q(transaction_date__year=parsed_date.year, transaction_date__month=parsed_date.month),
                        Q(expiry_date__year=parsed_date.year, expiry_date__month=parsed_date.month),
                        Q(date_added__year=parsed_date.year, date_added__month=parsed_date.month),
                    ])

        final_filter = Q()
        for condition in conditions:
            final_filter |= condition

        return self.filter(final_filter).distinct()


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
    balance = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    products = models.TextField()
    transaction_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    record_type = models.CharField(max_length=30, choices=RecordTypes.choices, default=RecordTypes.EXPENSE_RECEIPT)

    objects = RecordQuerySet.as_manager()

    def __str__(self):
        return self.title

    @property
    def badge_classes(self):
        return self.COLOR_MAP.get(self.record_type, self.COLOR_MAP[self.RecordTypes.OTHER.value])