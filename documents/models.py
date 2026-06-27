from django.contrib.auth.models import User
from django.db import models


class Document_data(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    filepath = models.CharField(max_length=500)
    date_added = models.DateTimeField(auto_now_add=True)

    associated_record = models.ForeignKey('records.Record', on_delete=models.CASCADE, blank=True, null=True)

    def __str__(self):
        return f"{self.filepath} associated with ({self.associated_record.title})"