from django.db.models.signals import pre_delete
from django.dispatch import receiver
from .models import Document_data
from .storage_helpers import s3
from django.conf import settings

# Pre-delete signal
@receiver(pre_delete, sender=Document_data)
def pre_delete_document(sender, instance, **kwargs):
    if instance.filepath:
        s3.delete_object(Bucket=settings.R2_STORAGE_BUCKET_NAME, Key=instance.filepath)