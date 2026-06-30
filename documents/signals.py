from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import Document_data
from .storage_helpers import s3
from django.conf import settings

# Post-delete signal
@receiver(post_delete, sender=Document_data) # Only delete if database query is deleted
def post_delete_document(sender, instance, **kwargs):
    if instance.filepath:
        s3.delete_object(Bucket=settings.R2_STORAGE_BUCKET_NAME, Key=instance.filepath)