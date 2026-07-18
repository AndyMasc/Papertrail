from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import format_html
from django.forms.utils import flatatt

from .models import Record, Folder


class FolderForm(forms.ModelForm):
    class Meta:
        model = Folder
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "w-full bg-white dark:bg-zinc-950 border border-zinc-200/50 dark:border-zinc-800/50 py-2.5 px-3.5 text-xs rounded-xl dark:text-zinc-100 text-zinc-900 placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:border-zinc-400 dark:focus:border-zinc-600 transition-all duration-150",
                    "placeholder": "e.g., General expenses, Vacation...",
                }
            )
        }


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
    folder = forms.ModelChoiceField(
        queryset=Folder.objects.none(),
        required=False,
        widget=forms.Select(
            attrs={
                "class": "w-full text-xs font-semibold bg-transparent border-transparent focus:outline-hidden cursor-pointer",
            }
        ),
    )

    class Meta:
        model = Record
        fields = [
            "title",
            "products",
            "merchant",
            "balance",
            "transaction_date",
            "expiry_date",
            "record_type",
            "notes",
            "folder",
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
    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if "folder" in self.fields:
            self.fields["folder"].queryset = Folder.objects.none()
            self.fields["folder"].required = False
            self.fields["folder"].empty_label = "Unfiled"
            if user is not None:
                self.fields["folder"].queryset = Folder.objects.filter(user=user)


class RecordUpdateForm(BaseRecordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance.user if self.instance and self.instance.pk else None
        if user:
            self.fields["folder"].queryset = Folder.objects.filter(user=user)
            self.fields["folder"].required = False
            self.fields["folder"].empty_label = "Unfiled"
