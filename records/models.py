from django.contrib.auth.models import User
from django.db import models

# Create your models here.

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

    COLOR_MAP = { # Themes for record_card.html badges
        RecordTypes.EXPENSE_RECEIPT: (
            "bg-emerald-500/10 text-emerald-700 border border-emerald-500/20 backdrop-blur-md "
            "dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/30"
        ),
        RecordTypes.VOUCHER: (
            "bg-amber-500/10 text-amber-700 border border-amber-500/20 backdrop-blur-md "
            "dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/30"
        ),
        RecordTypes.WARRANTY_CERTIFICATE: (
            "bg-green-500/10 text-green-700 border border-green-500/20 backdrop-blur-md "
            "dark:bg-green-500/10 dark:text-green-400 dark:border-green-500/30"
        ),
        RecordTypes.VENDOR_INVOICE: (
            "bg-blue-500/10 text-blue-700 border border-blue-500/20 backdrop-blur-md "
            "dark:bg-blue-500/10 dark:text-blue-400 dark:border-blue-500/30"
        ),
        RecordTypes.CUSTOMER_INVOICE: (
            "bg-indigo-500/10 text-indigo-700 border border-indigo-500/20 backdrop-blur-md "
            "dark:bg-indigo-500/10 dark:text-indigo-400 dark:border-indigo-500/30"
        ),
        RecordTypes.LOAN_DOCUMENT: (
            "bg-red-500/10 text-red-700 border border-red-500/20 backdrop-blur-md "
            "dark:bg-red-500/10 dark:text-red-400 dark:border-red-500/30"
        ),
        RecordTypes.CREDIT_CARD_STATEMENT: (
            "bg-sky-500/10 text-sky-700 border border-sky-500/20 backdrop-blur-md "
            "dark:bg-sky-500/10 dark:text-sky-400 dark:border-sky-500/30"
        ),
        RecordTypes.BANK_STATEMENT: (
            "bg-cyan-500/10 text-cyan-700 border border-cyan-500/20 backdrop-blur-md "
            "dark:bg-cyan-500/10 dark:text-cyan-400 dark:border-cyan-500/30"
        ),
        RecordTypes.PURCHASE_ORDER: (
            "bg-violet-500/10 text-violet-700 border border-violet-500/20 backdrop-blur-md "
            "dark:bg-violet-500/10 dark:text-violet-400 dark:border-violet-500/30"
        ),
        RecordTypes.PAYSLIP: (
            "bg-lime-500/10 text-lime-700 border border-lime-500/20 backdrop-blur-md "
            "dark:bg-lime-500/10 dark:text-lime-400 dark:border-lime-500/30"
        ),
        RecordTypes.TAX_DOCUMENT: (
            "bg-purple-500/10 text-purple-700 border border-purple-500/20 backdrop-blur-md "
            "dark:bg-purple-500/10 dark:text-purple-400 dark:border-purple-500/30"
        ),
        RecordTypes.SERVICE_CONTRACT: (
            "bg-teal-500/10 text-teal-700 border border-teal-500/20 backdrop-blur-md "
            "dark:bg-teal-500/10 dark:text-teal-400 dark:border-teal-500/30"
        ),
        RecordTypes.LEASE_AGREEMENT: (
            "bg-orange-500/10 text-orange-700 border border-orange-500/20 backdrop-blur-md "
            "dark:bg-orange-500/10 dark:text-orange-400 dark:border-orange-500/30"
        ),
        RecordTypes.INSURANCE_POLICY: (
            "bg-rose-500/10 text-rose-700 border border-rose-500/20 backdrop-blur-md "
            "dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/30"
        ),
        RecordTypes.OTHER: (
            "bg-slate-500/10 text-slate-700 border border-slate-500/20 backdrop-blur-md "
            "dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30"
        ),
    }
     
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date_added = models.DateField(auto_now_add=True) # Set to current date/time when record is added only
    last_edited = models.DateTimeField(auto_now=True) # Set to current date/time when record is edited
    is_active = models.BooleanField(default=True)

    title = models.CharField(max_length=255)
    merchant = models.CharField(max_length=255, blank=True, null=True)
    balance = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)  # Balance on record (whether coupon, reciept, warranty...)
    products = models.TextField()
    transaction_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    record_type = models.CharField(max_length=30, choices=RecordTypes.choices, default=RecordTypes.EXPENSE_RECEIPT)

    def __str__(self):
        return self.title

    @property
    def badge_classes(self):
        try:
            enum_type = self.RecordTypes(self.record_type)
            return self.COLOR_MAP.get(enum_type, self.COLOR_MAP[self.RecordTypes.OTHER])
        except ValueError:
            return self.COLOR_MAP[self.RecordTypes.OTHER]