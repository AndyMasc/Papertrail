from django.contrib.auth.models import User
from django.db import models


class document_data(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    filepath = models.CharField(max_length=255)
    date_added = models.DateTimeField(auto_now_add=True)
