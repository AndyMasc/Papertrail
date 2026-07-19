from django.db import models


class PlaidItem(models.Model):
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="plaid_items")
    item_id = models.CharField(max_length=255, unique=True)
    access_token = models.CharField(max_length=255)
    next_cursor = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_error_code = models.CharField(max_length=255, null=True, blank=True)
    last_error_message = models.TextField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    institution_name = models.CharField(max_length=255, null=True, blank=True)
    accounts_data = models.JSONField(null=True, blank=True)
