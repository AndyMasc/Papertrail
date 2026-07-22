"""Django models for storing Plaid banking integration data.

Tracks linked bank items, access tokens, sync cursors, and error
state needed to maintain ongoing transaction synchronization.
"""

from django.db import models


class PlaidItem(models.Model):
    """Represents a connected Plaid bank item (e.g. one bank account).

    Stores the access token and sync cursor needed to fetch transactions
    incrementally via the Plaid Transactions Sync endpoint. Also tracks
    institution metadata and error state for user-facing diagnostics.
    """

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

    def __str__(self) -> str:
        label = self.institution_name or self.item_id
        return f"{label} ({self.item_id})"
