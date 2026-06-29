from django import forms
from .models import Record

class AddRecordForm(forms.ModelForm):
    title = forms.CharField(max_length=255, required=True)
    products = forms.CharField(widget=forms.Textarea, max_length=500, required=True)
    merchant = forms.CharField(max_length=255, required=False)
    balance = forms.DecimalField(max_digits=10, decimal_places=2, required=False)
    transaction_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False)
    expiry_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False)

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
    
    record_type = forms.ChoiceField(
        choices=record_type_choices, initial="expense_receipt"
    )

    class Meta:
        model = Record
        fields = ['title', 'products', 'merchant', 'balance', 'transaction_date', 'expiry_date', 'record_type']