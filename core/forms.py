from allauth.account.forms import SignupForm
from allauth.account.forms import LoginForm
from django import forms
from django.db.models.base import F
from .models import UserSettings


class PasswordlessSignupForm(SignupForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "password1" in self.fields:
            del self.fields["password1"]
        if "password2" in self.fields:
            del self.fields["password2"]

    def save(self, request):
        user = super().save(request)
        
        # Mark the password as unusable in the database so traditional login fails
        user.set_unusable_password()
        user.save()
        return user

class PasswordlessLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "password" in self.fields:
            del self.fields["password"]


class UpdateUserSettingsForm(forms.ModelForm):
    auto_archive_expired_records = forms.BooleanField(required=False)
    auto_delete_archived_records = forms.BooleanField(required=False)
    class Meta:
        model = UserSettings
        fields = '__all__'
        exclude = ('user',)