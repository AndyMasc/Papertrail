from django.contrib.auth.models import User
from django.db import models

record_type_choices = [ # for Record model
    ("expense_receipt", "Expense Receipt"),
    ("voucher", "Voucher"),
    ("warranty_certificate", "Warranty Certificate"),
    ("vendor_invoice", "Vendor Invoice"),
    ("customer_invoice", "Customer Invoice"),
    ("loan_document", "Loan Document"),
    ("credit_card_statement", "Credit Card Statement"),
    ("bank_statement", "Bank Statement"),
    ("purchase_order", "Purchase Order"),
    ("payslip", "Payslip / Salary"),
    ("tax_document", "Tax Document"),
    ("service_contract", "Service Contract"),
    ("lease_agreement", "Lease / Rental Agreement"),
    ("insurance_policy", "Insurance Policy"),
    ("other", "Other"),
]

# Create your models here.

class Record(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date_added = models.DateField(auto_now_add=True)

    title = models.CharField(max_length=255)
    merchant = models.CharField(max_length=255, blank=True, null=True)
    balance = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)  # Balance on record (whether coupon, reciept, warranty...)
    products = models.TextField()
    transaction_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    notes = models.TextField(blank=True, null=True)
    
    is_active = models.BooleanField(default=True)

    record_type = models.CharField(
        max_length=30, choices=record_type_choices, default="expense_receipt"
    )

    def __str__(self):
        return self.title
