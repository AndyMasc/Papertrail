from django import forms
from .models import Document_data

class R2UploadForm(forms.Form):
    filename = forms.CharField(max_length=255, required=True)
    content_type = forms.CharField(max_length=100, required=True)
    notes = forms.CharField(required=False)

class DocumentUpdateForm(forms.ModelForm):
    class Meta:
        model = Document_data
        fields = ["title", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)