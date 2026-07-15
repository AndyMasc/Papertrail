from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError

from .models import DocumentData


class R2UploadForm(forms.Form):
    filename = forms.CharField(max_length=255, required=True)
    content_type = forms.CharField(max_length=100, required=True)
    notes = forms.CharField(required=False)

    def clean_filename(self):
        filename = Path(self.cleaned_data["filename"]).name
        if not filename or filename in {".", ".."}:
            raise ValidationError("Invalid filename.")
        return filename

    def clean_content_type(self):
        content_type = self.cleaned_data["content_type"].lower().split(";")[0].strip()
        allowed = {
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/heic",
            "image/heif",
        }
        if content_type not in allowed:
            raise ValidationError("Unsupported content type.")
        return content_type


class DocumentUpdateForm(forms.ModelForm):
    class Meta:
        model = DocumentData
        fields = ["title", "notes", "associated_record"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["associated_record"].required = False
        self.fields["associated_record"].queryset = self.fields[
            "associated_record"
        ].queryset.active()
