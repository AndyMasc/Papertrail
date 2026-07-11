from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import Document_data
from . import tasks
from django.db import transaction

@receiver(post_delete, sender=Document_data) # Only delete if database query is deleted
def post_delete_document(sender, instance, **kwargs):
    if instance.filepath:
        transaction.on_commit(lambda: tasks.delete_document.delay(instance.filepath))