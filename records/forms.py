from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import format_html
from django.forms.utils import flatatt

from .models import Record


class TrimmedTextarea(forms.Textarea):
    def render(self, name, value, attrs=None, renderer=None):
        if value is None:
            value = ""
        attrs = self.build_attrs(self.attrs, attrs)
        attrs["name"] = name
        return format_html("<textarea{}>{}</textarea>", flatatt(attrs), value)


class BaseRecordForm(forms.ModelForm):
    title = forms.CharField(max_length=255, required=True)
    products = forms.CharField(widget=TrimmedTextarea, max_length=500, required=True)
    merchant = forms.CharField(max_length=255, required=False)
    balance = forms.DecimalField(max_digits=10, decimal_places=2, required=False)
    transaction_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}), required=False
    )
    expiry_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}), required=False
    )
    record_type = forms.ChoiceField(
        choices=Record.RecordTypes.choices,
        required=True,
    )
    notes = forms.CharField(widget=TrimmedTextarea, required=False, max_length=500)

    class Meta:
        model = Record
        fields = "__all__"
        exclude = [
            "user",
            "date_added",
            "last_edited",
            "is_active",
            "expiry_notification_sent",
        ]

    def clean_transaction_date(self):
        transaction_date = self.cleaned_data.get("transaction_date")
        if transaction_date and transaction_date > timezone.localdate():
            raise ValidationError("Transaction date cannot be in the future.")
        return transaction_date

    def clean_balance(self):
        balance = self.cleaned_data.get("balance")
        if balance is not None and balance < 0:
            raise ValidationError("Balance cannot be negative.")
        return balance

    def clean(self):
        cleaned_data = super().clean()
        expiry_date = cleaned_data.get("expiry_date")
        transaction_date = cleaned_data.get("transaction_date")
        if expiry_date and transaction_date and expiry_date < transaction_date:
            raise ValidationError(
                {"expiry_date": "Expiry date cannot be before transaction date."}
            )
        return cleaned_data


class AddRecordForm(BaseRecordForm):
    pass


class RecordUpdateForm(BaseRecordForm):
    pass
