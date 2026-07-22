"""Django signals for automatic UserSettings provisioning.

Listens for new User creation and ensures every user starts with a
sensible set of default preferences.
"""

from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserSettings


@receiver(post_save, sender=User)
def create_user_settings(sender, instance, created, **kwargs):  # noqa: ARG001
    """Create a default UserSettings row whenever a new User is saved."""
    if created:
        UserSettings.objects.create(user=instance)
