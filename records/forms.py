from django import forms
from .models import Record
from django.utils import timezone
from django.core.exceptions import ValidationError

class AddRecordForm(forms.ModelForm):
    title = forms.CharField(max_length=255, required=True)
    products = forms.CharField(widget=forms.Textarea, max_length=500, required=True)
    merchant = forms.CharField(max_length=255, required=False)
    balance = forms.DecimalField(max_digits=10, decimal_places=2, required=False)
    transaction_date = forms.DateField(widget=forms.DateInput(attrs={'type':'date'}), required=False)
    expiry_date = forms.DateField(widget=forms.DateInput(attrs={'type':'date'}), required=False)
    
    record_type = forms.ChoiceField(
        choices=Record.RecordTypes.choices, 
        required=True, 
    )

    notes = forms.CharField(widget=forms.Textarea, required=False, max_length=500)

    class Meta:
        model = Record
        fields = '__all__'
        exclude = ['user', 'date_added', 'last_edited', 'is_active']

    def clean_transaction_date(self):
        transaction_date = self.cleaned_data.get('transaction_date')
        if transaction_date and transaction_date > timezone.localdate():
            raise ValidationError('Transaction date cannot be in the future.')
        return transaction_date

    def clean(self): # clean is a method name. Only works with clean_<field_name>, where field_name is from model
        cleaned_data = super().clean()
        expiry_date = cleaned_data.get('expiry_date')
        transaction_date = cleaned_data.get('transaction_date')
        if (expiry_date and transaction_date) and (expiry_date < transaction_date):
            raise ValidationError({'expiry_date': 'Expiry date cannot be before transaction date.'})
        return cleaned_data

class RecordUpdateForm(AddRecordForm):
    class Meta(AddRecordForm.Meta):
        pass