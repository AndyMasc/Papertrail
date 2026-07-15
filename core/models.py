from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()


class UserSettings(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="settings",
    )
    auto_archive_expired_records = models.BooleanField(default=True)
    auto_delete_archived_records = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Settings"
        verbose_name_plural = "User Settings"

    def __str__(self):
        return f"Settings for {self.user.email}"
