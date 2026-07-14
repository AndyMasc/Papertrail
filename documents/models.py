from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
import os
import hashlib

class DocumentDataQuerySet(models.QuerySet):
    def filter_by_user(self, user):
        return self.filter(user=user)

    def search(self, query):
        if not query:
            return self
            
        query = query.strip()
        return self.filter(
            Q(title__icontains=query) | 
            Q(notes__icontains=query)
        )

class DocumentData(models.Model):
    title = models.CharField(max_length=200, default="Untitled")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    filepath = models.CharField(max_length=500)
    date_added = models.DateTimeField(auto_now_add=True)
    associated_record = models.ForeignKey('records.Record', on_delete=models.CASCADE, blank=True, null=True, related_name='documents')
    is_main = models.BooleanField(default=False)
    notes = models.TextField(blank=True, null=True)
    file_extension = models.CharField(max_length=10, blank=True, null=True)
    file_hash = models.CharField(max_length=64)

    objects = DocumentDataQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["user", "associated_record"]),
            models.Index(fields=["user", "file_extension"]),
            models.Index(fields=["date_added", "file_hash"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'file_hash'], 
                name='unique_user_file_hash'
            )
        ]

    def save(self, *args, **kwargs): # Save file extension on save
        if self.filepath:
            _, ext = os.path.splitext(self.filepath)
            self.file_extension = ext.replace('.', '').strip().lower()[:10]
            
        super().save(*args, **kwargs)

    @staticmethod
    def calculate_hash(file_buffer):
        sha256 = hashlib.sha256()
        file_buffer.seek(0)
        for chunk in iter(lambda: file_buffer.read(65536), b""):
            sha256.update(chunk)
        file_buffer.seek(0)
        return sha256.hexdigest()

    def __str__(self):
        return f"{self.filepath}"