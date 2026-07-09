from allauth.account.forms import SignupForm
from allauth.account.forms import LoginForm

class PasswordlessSignupForm(SignupForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Strip the password fields out of the form fields dictionary
        if "password1" in self.fields:
            del self.fields["password1"]
        if "password2" in self.fields:
            del self.fields["password2"]

    def save(self, request):
        # Let the default BaseSignupForm.save() run its logic without passwords
        user = super().save(request)
        
        # Mark the password as unusable in the database so traditional login fails
        user.set_unusable_password()
        user.save()
        return user

class PasswordlessLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Strip out the password field completely
        if "password" in self.fields:
            del self.fields["password"]