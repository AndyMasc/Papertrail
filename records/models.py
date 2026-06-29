from django.contrib.auth.models import User
from django.db import models

# Create your models here.


class Record(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date_added = models.DateField(auto_now_add=True)

    description = models.TextField(blank=True, null=True)
    title = models.CharField(max_length=255)
    merchant = models.CharField(max_length=255, blank=True, null=True)
    balance = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True
    )  # Balance on record (whether coupon, reciept, warranty...)
    products = models.TextField()
    transaction_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    record_type_choices = [
        ("expense_receipt", "Expense Receipt"),
        ("vendor_invoice", "Vendor Invoice"),
        ("purchase_order", "Purchase Order"),
        ("service_contract", "Service Contract / Warranty"),
        ("corporate_credit", "Corporate Credit / Voucher"),
        ("tax_document", "Tax Document"),
        ("gift_voucher", "Gift Voucher"),
        ("other", "Other"),
    ]
    record_type = models.CharField(
        max_length=20, choices=record_type_choices, default="expense_receipt"
    )

    def __str__(self):
        return self.title
