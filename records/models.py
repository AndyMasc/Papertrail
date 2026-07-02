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

    COLOR_MAP = { # For badge colors in record_card.html
         RecordTypes.EXPENSE_RECEIPT: "bg-emerald-500/10 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400 border-emerald-500/20",
         RecordTypes.VOUCHER: "bg-amber-500/10 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400 border-amber-500/20",
         RecordTypes.WARRANTY_CERTIFICATE: "bg-green-500/10 text-green-600 dark:bg-green-500/10 dark:text-green-400 border-green-500/20",
         RecordTypes.VENDOR_INVOICE: "bg-blue-500/10 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400 border-blue-500/20",
         RecordTypes.CUSTOMER_INVOICE: "bg-indigo-500/10 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400 border-indigo-500/20",
         RecordTypes.LOAN_DOCUMENT: "bg-red-500/10 text-red-600 dark:bg-red-500/10 dark:text-red-400 border-red-500/20",
         RecordTypes.CREDIT_CARD_STATEMENT: "bg-sky-500/10 text-sky-600 dark:bg-sky-500/10 dark:text-sky-400 border-sky-500/20",
         RecordTypes.BANK_STATEMENT: "bg-cyan-500/10 text-cyan-600 dark:bg-cyan-500/10 dark:text-cyan-400 border-cyan-500/20",
         RecordTypes.PURCHASE_ORDER: "bg-violet-500/10 text-violet-600 dark:bg-violet-500/10 dark:text-violet-400 border-violet-500/20",
         RecordTypes.PAYSLIP: "bg-lime-500/10 text-lime-600 dark:bg-lime-500/10 dark:text-lime-400 border-lime-500/20",
         RecordTypes.TAX_DOCUMENT: "bg-purple-500/10 text-purple-600 dark:bg-purple-500/10 dark:text-purple-400 border-purple-500/20",
         RecordTypes.SERVICE_CONTRACT: "bg-teal-500/10 text-teal-600 dark:bg-teal-500/10 dark:text-teal-400 border-teal-500/20",
         RecordTypes.LEASE_AGREEMENT: "bg-orange-500/10 text-orange-600 dark:bg-orange-500/10 dark:text-orange-400 border-orange-500/20",
         RecordTypes.INSURANCE_POLICY: "bg-rose-500/10 text-rose-600 dark:bg-rose-500/10 dark:text-rose-400 border-rose-500/20",
         RecordTypes.OTHER: "bg-slate-500/10 text-slate-600 dark:bg-slate-500/10 dark:text-slate-400 border-slate-500/20",
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