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
    enable_push_notifications = models.BooleanField(default=True)
    enable_email_notifications = models.BooleanField(default=True)
    auto_create_and_organize_folders = models.BooleanField(default=True)

    class AdvanceTimeChoices(models.TextChoices):
        ONE_DAY = "1", "1 Day"
        THREE_DAYS = "3", "3 Days"
        ONE_WEEK = "7", "1 Week"
        ONE_MONTH = "30", "1 Month"

    expiring_notifications_advance_time = models.CharField(
        max_length=2,
        choices=AdvanceTimeChoices.choices,
        default=AdvanceTimeChoices.THREE_DAYS,
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Settings"
        verbose_name_plural = "User Settings"

    def __str__(self):
        return f"Settings for {self.user.email}"


class Notification(models.Model):
    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    subject = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)
