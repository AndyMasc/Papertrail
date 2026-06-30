from django.contrib.auth.models import User
from django.db import models


class Document_data(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    filepath = models.CharField(max_length=500)
    date_added = models.DateTimeField(auto_now_add=True)
    associated_record = models.ForeignKey('records.Record', on_delete=models.CASCADE, blank=True, null=True, related_name='documents')

    status_states = (
        ('pending', 'Pending'),
        ('processed', 'Processed'),
        ('attached', 'Attached'),
        ('error', 'Error'),
    )
    status = models.CharField(max_length=50, choices=status_states, default='pending')

    def __str__(self):
        return f"{self.filepath}"