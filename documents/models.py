import os

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q


class DocumentStatus(models.TextChoices):
    PENDING_UPLOAD = "pending_upload", "Pending Upload"
    UPLOADED = "uploaded", "Uploaded"
    PROCESSING = "processing", "Processing OCR"
    COMPLETED = "completed", "Completed"
    ERROR = "error", "Error"
    DELETING = "deleting", "Deleting"


class DocumentDataQuerySet(models.QuerySet):
    def search(self, query):
        if not (query := query.strip()):
            return self
        return self.filter(Q(title__icontains=query) | Q(notes__icontains=query))


class DocumentData(models.Model):
    title = models.CharField(max_length=200, default="Untitled")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    filepath = models.CharField(max_length=500)
    date_added = models.DateTimeField(auto_now_add=True)
    associated_record = models.ForeignKey(
        "records.Record",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="documents",
    )
    did_ocr = models.BooleanField(default=False)
    notes = models.TextField(blank=True, null=True)
    file_extension = models.CharField(max_length=10, blank=True, null=True)
    file_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=20,
        choices=DocumentStatus.choices,
        default=DocumentStatus.PENDING_UPLOAD,
        db_index=False,
    )

    objects = DocumentDataQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["user", "associated_record"]),
            models.Index(fields=["user", "file_extension"]),
            models.Index(fields=["date_added", "file_hash"]),
            models.Index(
                fields=["user", "status"],
                name="idx_doc_user_status",
            ),
            models.Index(
                fields=["user", "-date_added"],
                name="idx_doc_list_cover",
            ),
            models.Index(
                fields=["user", "did_ocr", "-date_added"],
                name="idx_doc_main_cover",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "file_hash"], name="unique_user_file_hash"
            )
        ]

    def save(self, *args, **kwargs):
        if self.filepath and not self.file_extension:
            _, ext = os.path.splitext(self.filepath)
            self.file_extension = ext.replace(".", "").strip().lower()[:10]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.filepath}"
