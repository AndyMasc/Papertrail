import logging
from typing import Any

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.db.models import Q
from django_qstash import shared_task
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from records.models import Folder, Record

from .models import PlaidItem
from .plaid_client import client

logger: logging.Logger = logging.getLogger(__name__)


def choose_folder(
    user: User, category: str | None, folder_cache: dict[str, Folder] | None = None
) -> Folder | None:
    if not category:
        return None

    category_clean: str = category.strip()

    if folder_cache is not None and category_clean in folder_cache:
        return folder_cache[category_clean]

    key_words = [word.strip().lower() for word in category_clean.split() if len(word.strip()) > 0]
    if not key_words:
        return None

    query = Q()
    for word in key_words:
        query |= Q(name__iregex=rf"\y{word}\y")

    folder = Folder.objects.filter(query, user=user).first()

    if not folder:
        try:
            folder, created = Folder.objects.get_or_create(user=user, name=category_clean)
        except IntegrityError:
            folder = Folder.objects.filter(user=user, name=category_clean).first()

    if folder_cache is not None and folder:
        folder_cache[category_clean] = folder

    return folder


def _txn_to_record_defaults(
    txn: dict[str, Any], plaid_item: PlaidItem, folder_cache: dict[str, Folder] | None = None
) -> dict[str, Any]:
    categories = txn.get("category") or []
    primary_category = categories[0] if categories else ""
    user = plaid_item.user

    auto_create_enabled = getattr(user.settings, "auto_create_and_organize_folders", True)

    matched_folder = None
    if auto_create_enabled:
        matched_folder = choose_folder(user, primary_category, folder_cache=folder_cache)

    return {
        "user": user,
        "plaid_item": plaid_item,
        "title": txn["name"],
        "merchant": txn.get("merchant_name") or txn["name"],
        "balance": abs(txn["amount"]),
        "transaction_date": txn.get("authorized_date") or txn["date"],
        "record_type": Record.RecordTypes.FINANCIAL_DOCUMENT,
        "notes": primary_category,
        "folder": matched_folder,
    }


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_and_convert_for_item_task(self, plaid_item_id: int | str) -> dict[str, Any]:
    try:
        plaid_item: PlaidItem = PlaidItem.objects.get(id=plaid_item_id)
    except PlaidItem.DoesNotExist:
        return {"error": f"PlaidItem {plaid_item_id} not found"}

    cursor: str = plaid_item.next_cursor or ""
    has_more: bool = True
    stats: dict[str, int] = {"added": 0, "modified": 0, "removed": 0}
    folder_cache: dict[str, Folder] = {}

    while has_more:
        try:
            response: Any = client.transactions_sync(
                TransactionsSyncRequest(
                    access_token=plaid_item.access_token,
                    cursor=cursor,
                )
            )
        except Exception as e:
            logger.warning(
                "Plaid API error or rate limit hit for item %s. Retrying task.", plaid_item_id
            )
            countdown = self.default_retry_delay * (2**self.request.retries)
            raise self.retry(exc=e, countdown=countdown) from None

        data: dict[str, Any] = response if isinstance(response, dict) else response.to_dict()

        with db_transaction.atomic():
            txn: dict[str, Any]
            for txn in data.get("removed", []):
                deleted, _ = Record.objects.filter(
                    plaid_transaction_id=txn["transaction_id"]
                ).delete()
                stats["removed"] += deleted

            for txn in data.get("added", []):
                Record.objects.update_or_create(
                    plaid_transaction_id=txn["transaction_id"],
                    defaults=_txn_to_record_defaults(txn, plaid_item, folder_cache),
                )
                stats["added"] += 1

            for txn in data.get("modified", []):
                Record.objects.update_or_create(
                    plaid_transaction_id=txn["transaction_id"],
                    defaults=_txn_to_record_defaults(txn, plaid_item, folder_cache),
                )
                stats["modified"] += 1

            cursor = data.get("next_cursor", cursor)
            has_more = data.get("has_more", False)

            plaid_item.next_cursor = cursor
            plaid_item.save(update_fields=["next_cursor"])

    try:
        from records.matching import try_match_plaid_record

        for plaid_record in (
            Record.objects.filter(plaid_item=plaid_item, is_active=True)
            .only("pk", "user_id")
            .iterator(chunk_size=500)
        ):
            try_match_plaid_record(plaid_record)
    except Exception:
        logger.exception("Error matching plaid records to documents for item %s", plaid_item_id)

    return {"status": "synced", **stats}
