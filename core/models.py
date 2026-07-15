from django.contrib.auth.models import User
from django.db import models


class UserSettings(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="settings")
    auto_archive_expired_records = models.BooleanField(default=True)
    auto_delete_archived_records = models.BooleanField(default=True)

    def __str__(self):
        return f"Settings for {self.user.email}"
