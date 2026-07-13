from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
import os

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

    objects = DocumentDataQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["user", "associated_record"]),
            models.Index(fields=["user", "file_extension"]),
            models.Index(fields=["date_added"]),
        ]

    def save(self, *args, **kwargs): # Save file extension on save
        if self.filepath:
            _, ext = os.path.splitext(self.filepath)
            self.file_extension = ext.replace('.', '').strip().lower()[:10]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.filepath}"