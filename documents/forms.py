from django import forms
from pathlib import Path
from .models import DocumentData
from django.core.exceptions import ValidationError

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}


class R2UploadForm(forms.Form):
    filename = forms.CharField(max_length=255, required=True)
    content_type = forms.CharField(max_length=100, required=True)
    notes = forms.CharField(required=False)

    def clean_filename(self):
        filename = Path(self.cleaned_data["filename"]).name
        if not filename or filename in {".", ".."}:
            raise ValidationError("Invalid filename.")
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise ValidationError(
                "Unsupported file type. Allowed: PDF, JPEG, PNG, WebP, HEIC."
            )
        return filename

    def clean_content_type(self):
        content_type = self.cleaned_data["content_type"].lower().split(";")[0].strip()
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValidationError("Unsupported content type.")
        return content_type


class DocumentUpdateForm(forms.ModelForm):
    class Meta:
        model = DocumentData
        fields = ["title", "notes", "associated_record"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["associated_record"].required = False
