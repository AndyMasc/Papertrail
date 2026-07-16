from allauth.account.forms import LoginForm, SignupForm
from django import forms

from .models import UserSettings


class PasswordlessSignupForm(SignupForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("password1", None)
        self.fields.pop("password2", None)

    def save(self, request):
        user = super().save(request)
        user.set_unusable_password()
        user.save()
        return user


class PasswordlessLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("password", None)


class UpdateUserSettingsForm(forms.ModelForm):
    class Meta:
        model = UserSettings
        fields = [
            "auto_archive_expired_records",
            "auto_delete_archived_records",
            "expiring_notifications_advance_time",
        ]
