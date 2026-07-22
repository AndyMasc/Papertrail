"""Django signals for automatic R2 cleanup on document deletion.

When a DocumentData record is deleted from the database, the associated
R2 file is asynchronously removed via a background task on transaction commit.
"""

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from . import tasks
from .models import DocumentData


@receiver(post_delete, sender=DocumentData)
def post_delete_document(sender, instance, **kwargs):  # noqa: ARG001
    """Queue R2 file deletion after the database commit succeeds."""
    if instance.filepath:
        transaction.on_commit(lambda: tasks.delete_document.delay(instance.filepath))
