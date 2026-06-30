from django import forms
from .models import Record
from .models import record_type_choices

class AddRecordForm(forms.ModelForm):
    title = forms.CharField(max_length=255, required=True)
    products = forms.CharField(widget=forms.Textarea, max_length=500, required=True)
    merchant = forms.CharField(max_length=255, required=False)
    balance = forms.DecimalField(max_digits=10, decimal_places=2, required=False)
    transaction_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False)
    expiry_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False)
    record_type = forms.ChoiceField(choices=record_type_choices, initial="expense_receipt")
    notes = forms.CharField(widget=forms.Textarea, required=False, max_length=500)

    class Meta:
        model = Record
        fields = ['title', 'products', 'merchant', 'balance', 'transaction_date', 'expiry_date', 'record_type', 'notes']