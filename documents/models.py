import os

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

User = settings.AUTH_USER_MODEL


class DocumentStatus(models.TextChoices):
    PENDING_UPLOAD = "pending_upload", "Pending Upload"
    UPLOADED = "uploaded", "Uploaded"
    PROCESSING = "processing", "Processing OCR"
    COMPLETED = "completed", "Completed"
    ERROR = "error", "Error"
    DELETING = "deleting", "Deleting"


class DocumentDataQuerySet(models.QuerySet):
    def for_user(self, user) -> "DocumentDataQuerySet":
        return self.filter(user=user)

    def orphaned(self) -> "DocumentDataQuerySet":
        return self.filter(associated_record__isnull=True)

    def linked(self) -> "DocumentDataQuerySet":
        return self.filter(associated_record__isnull=False)

    def by_status(self, status: str) -> "DocumentDataQuerySet":
        return self.filter(status=status)

    def pending(self) -> "DocumentDataQuerySet":
        return self.by_status(DocumentStatus.PENDING_UPLOAD)

    def processing(self) -> "DocumentDataQuerySet":
        return self.by_status(DocumentStatus.PROCESSING)

    def completed(self) -> "DocumentDataQuerySet":
        return self.by_status(DocumentStatus.COMPLETED)

    def errored(self) -> "DocumentDataQuerySet":
        return self.by_status(DocumentStatus.ERROR)

    def stale_pending(self, minutes: int = 30) -> "DocumentDataQuerySet":
        cutoff = timezone.now() - timezone.timedelta(minutes=minutes)
        return self.pending().filter(date_added__lt=cutoff)

    def stale_error(self, days: int = 2) -> "DocumentDataQuerySet":
        cutoff = timezone.now() - timezone.timedelta(days=days)
        return self.errored().filter(date_added__lt=cutoff)

    def search(self, query: str) -> "DocumentDataQuerySet":
        if not (query := query.strip()):
            return self
        return self.filter(Q(title__icontains=query) | Q(notes__icontains=query))

    def with_record(self) -> "DocumentDataQuerySet":
        return self.select_related("associated_record")


class DocumentDataManager(models.Manager.from_queryset(DocumentDataQuerySet)):
    pass


class DocumentData(models.Model):
    id = models.BigAutoField(primary_key=True)
    title = models.CharField(max_length=200, default="Untitled")
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    filepath = models.CharField(max_length=500)
    date_added = models.DateTimeField(auto_now_add=True, db_index=True)
    associated_record = models.ForeignKey(
        "records.Record",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="documents",
    )
    did_ocr = models.BooleanField(default=False)
    ocr_retries = models.PositiveSmallIntegerField(default=0)
    ocr_error = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    file_extension = models.CharField(max_length=10, blank=True, default="")
    file_hash = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=DocumentStatus.choices,
        default=DocumentStatus.PENDING_UPLOAD,
        db_index=True,
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DocumentDataManager()

    class Meta:
        ordering = ["-date_added"]
        indexes = [
            models.Index(fields=["user", "associated_record"], name="idx_doc_user_record"),
            models.Index(fields=["user", "file_extension"], name="idx_doc_user_ext"),
            models.Index(fields=["date_added", "file_hash"], name="idx_doc_date_hash"),
            models.Index(fields=["user", "status"], name="idx_doc_user_status"),
            models.Index(fields=["user", "-date_added"], name="idx_doc_list_cover"),
            models.Index(fields=["user", "did_ocr", "-date_added"], name="idx_doc_main_cover"),
            models.Index(
                fields=["associated_record", "date_added"],
                name="idx_doc_orphaned_cleanup",
            ),
            models.Index(
                fields=["status", "date_added", "filepath"],
                name="idx_doc_reconcile_pending",
            ),
            models.Index(fields=["status", "date_added"], name="idx_doc_reconcile_error"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "file_hash"],
                name="unique_user_file_hash",
            )
        ]

    def save(self, *args, **kwargs):
        if self.filepath and not self.file_extension:
            _, ext = os.path.splitext(self.filepath)
            normalized = ext.replace(".", "").strip().lower()[:10]
            if normalized:
                self.file_extension = normalized
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.filepath}"

    @property
    def is_processing(self) -> bool:
        return self.status in (
            DocumentStatus.PENDING_UPLOAD,
            DocumentStatus.UPLOADED,
            DocumentStatus.PROCESSING,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in (DocumentStatus.COMPLETED, DocumentStatus.ERROR)

    @property
    def presigned_view_url(self) -> str:
        from .storage import generate_read_presigned_url

        return generate_read_presigned_url(self.filepath)
