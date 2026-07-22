import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

import rapidfuzz
from django.db import transaction as db_transaction
from django.db.models.query import QuerySet
from django.utils import timezone

from documents.models import DocumentData
from records.models import MergeLog, Record

logger = logging.getLogger(__name__)

BALANCE_TOLERANCE = Decimal("1.00")
DATE_TOLERANCE_DAYS = 3
MATCH_LOOKAHEAD_DAYS = 14
MERGE_SCORE_THRESHOLD = 55


def _normalize(text: str) -> str:
    return text.lower().strip()


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a = _normalize(a)
    b = _normalize(b)
    if len(a) < 3 or len(b) < 3:
        return float(a == b)
    max_len = max(len(a), len(b))
    if max_len > 0 and abs(len(a) - len(b)) / max_len > 0.6:
        return 0.0
    return rapidfuzz.fuzz.ratio(a, b) / 100.0


def _balance_diff(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    if a is None or b is None:
        return None
    return abs(a - b)


def calculate_match_score(record_a: Record, record_b: Record) -> int:
    score = 0

    diff = _balance_diff(record_a.balance, record_b.balance)
    if diff is not None:
        if diff == 0:
            score += 40
        elif diff <= BALANCE_TOLERANCE:
            score += 30
        elif diff <= BALANCE_TOLERANCE * 3:
            score += 15

    if record_a.transaction_date and record_b.transaction_date:
        date_diff = abs((record_a.transaction_date - record_b.transaction_date).days)
        if date_diff == 0:
            score += 30
        elif date_diff == 1:
            score += 20
        elif date_diff <= DATE_TOLERANCE_DAYS:
            score += 10

    if record_a.merchant and record_b.merchant:
        sim = _similarity(record_a.merchant, record_b.merchant)
        if sim >= 0.9:
            score += 30
        elif sim >= 0.7:
            score += 20
        elif sim >= 0.5:
            score += 10
        elif sim >= 0.3:
            score += 5

    if record_a.title and record_b.title:
        sim = _similarity(record_a.title, record_b.title)
        if sim >= 0.9:
            score += 20
        elif sim >= 0.7:
            score += 15
        elif sim >= 0.5:
            score += 8
        elif sim >= 0.3:
            score += 3

    return score


def _apply_date_window(qs: QuerySet[Record], record: Record) -> QuerySet[Record]:
    if record.transaction_date:
        window_start = record.transaction_date - timedelta(days=MATCH_LOOKAHEAD_DAYS)
        window_end = record.transaction_date + timedelta(days=MATCH_LOOKAHEAD_DAYS)
        return qs.filter(transaction_date__range=(window_start, window_end))
    return qs


def find_best_plaid_match(record: Record) -> Record | None:
    qs = (
        Record.objects.filter(
            user=record.user,
            plaid_transaction_id__isnull=False,
            is_active=True,
        )
        .exclude(pk=record.pk)
        .select_related("folder", "user")
    )
    candidates = _apply_date_window(qs, record)

    best_score = 0
    best_match: Record | None = None

    for candidate in candidates.iterator(chunk_size=500):
        score = calculate_match_score(record, candidate)
        if score > best_score:
            best_score = score
            best_match = candidate
            if best_score >= 95:
                break

    if best_score >= MERGE_SCORE_THRESHOLD:
        logger.info(
            "Found plaid match for record %s: record %s (score=%d)",
            record.pk,
            best_match.pk,
            best_score,
        )
        return best_match

    return None


def find_document_matches_for_plaid(plaid_record: Record) -> list[tuple[Record, int]]:
    qs = (
        Record.objects.filter(
            user=plaid_record.user,
            plaid_transaction_id__isnull=True,
            is_active=True,
        )
        .exclude(pk=plaid_record.pk)
        .select_related("folder", "user")
    )
    doc_records = _apply_date_window(qs, plaid_record)

    results: list[tuple[Record, int]] = []

    for candidate in doc_records.iterator(chunk_size=500):
        score = calculate_match_score(plaid_record, candidate)
        if score >= MERGE_SCORE_THRESHOLD:
            results.append((candidate, score))

    results.sort(key=lambda x: -x[1])
    return results


def _record_snapshot(record: Record) -> dict[str, Any]:
    return {
        "products": record.products,
        "notes": record.notes,
        "record_type": record.record_type,
        "folder_id": record.folder_id,
        "is_active": record.is_active,
        "plaid_transaction_id": record.plaid_transaction_id,
        "title": record.title,
        "merchant": record.merchant,
        "balance": str(record.balance) if record.balance is not None else None,
        "transaction_date": record.transaction_date.isoformat()
        if record.transaction_date
        else None,
        "payment_method": record.payment_method,
    }


@db_transaction.atomic
def merge_document_into_plaid(
    plaid_record: Record,
    document_record: Record,
    document: DocumentData | None = None,
) -> Record | None:
    locked_plaid = Record.objects.select_for_update().get(pk=plaid_record.pk)
    fresh_doc = Record.objects.select_for_update().get(pk=document_record.pk)
    if not fresh_doc.is_active or fresh_doc.plaid_transaction_id is not None:
        logger.warning(
            "Document record %s is no longer mergable (is_active=%s, plaid_id=%r), skipping",
            document_record.pk,
            fresh_doc.is_active,
            fresh_doc.plaid_transaction_id,
        )
        return None

    plaid_snapshot = _record_snapshot(locked_plaid)
    document_snapshot = _record_snapshot(fresh_doc)

    locked_plaid._skip_auto_match = True
    fresh_doc._skip_auto_match = True

    doc_document_ids = list(
        DocumentData.objects.filter(associated_record=fresh_doc).values_list("pk", flat=True)
    )
    DocumentData.objects.filter(associated_record=fresh_doc).update(associated_record=locked_plaid)
    document_snapshot["document_ids"] = doc_document_ids

    if fresh_doc.products:
        locked_plaid.products = fresh_doc.products
    if fresh_doc.notes:
        locked_plaid.notes = fresh_doc.notes
    if fresh_doc.record_type != Record.RecordTypes.FINANCIAL_DOCUMENT:
        locked_plaid.record_type = fresh_doc.record_type
    if fresh_doc.folder_id:
        locked_plaid.folder_id = fresh_doc.folder_id

    locked_plaid.payment_method = locked_plaid.payment_method or fresh_doc.payment_method

    locked_plaid.save(
        update_fields=[
            "products",
            "notes",
            "record_type",
            "folder_id",
            "payment_method",
        ]
    )

    fresh_doc.is_active = False
    fresh_doc.save(update_fields=["is_active"])

    MergeLog.objects.create(
        plaid_record=locked_plaid,
        document_record=fresh_doc,
        document=document,
        plaid_snapshot=plaid_snapshot,
        document_snapshot=document_snapshot,
    )

    logger.info(
        "Merged document record %s into plaid record %s",
        fresh_doc.pk,
        locked_plaid.pk,
    )

    return locked_plaid


@db_transaction.atomic
def undo_merge(merge_log: MergeLog) -> Record | None:
    merge_log = MergeLog.objects.select_for_update().get(pk=merge_log.pk)
    if merge_log.undone_at:
        return None

    plaid_record = merge_log.plaid_record
    document_record = merge_log.document_record
    document = merge_log.document

    if document_record is None:
        logger.warning(
            "Cannot restore document record for merge %s — document_record was deleted",
            merge_log.pk,
        )

    if plaid_record and plaid_record.is_active:
        locked_plaid = Record.objects.select_for_update().get(pk=plaid_record.pk)
        locked_plaid._skip_auto_match = True
        snap = merge_log.plaid_snapshot
        locked_plaid.products = snap.get("products", "")
        locked_plaid.notes = snap.get("notes", "")
        locked_plaid.record_type = snap.get("record_type", Record.RecordTypes.FINANCIAL_DOCUMENT)
        locked_plaid.folder_id = snap.get("folder_id")
        locked_plaid.payment_method = snap.get("payment_method", "")
        locked_plaid.save(
            update_fields=[
                "products",
                "notes",
                "record_type",
                "folder_id",
                "payment_method",
            ]
        )

    if document_record:
        locked_doc = Record.objects.select_for_update().get(pk=document_record.pk)
        locked_doc._skip_auto_match = True
        locked_doc.is_active = True
        locked_doc.plaid_transaction_id = None
        locked_doc.save(update_fields=["is_active", "plaid_transaction_id"])

    doc_ids: list[int] | None = merge_log.document_snapshot.get("document_ids")
    if doc_ids and plaid_record and document_record:
        DocumentData.objects.filter(pk__in=doc_ids).update(associated_record=document_record)
    elif document and document_record:
        document.associated_record = document_record
        document.save(update_fields=["associated_record"])

    merge_log.undone_at = timezone.now()
    merge_log.save(update_fields=["undone_at"])

    logger.info("Undone merge %s", merge_log.pk)
    return document_record


def try_match_document_record(
    document_record: Record,
    document: DocumentData | None = None,
) -> Record | None:
    plaid_match = find_best_plaid_match(document_record)
    if plaid_match is None:
        return None

    return merge_document_into_plaid(plaid_match, document_record, document)


def try_match_plaid_record(plaid_record: Record) -> list[Record]:
    matches = find_document_matches_for_plaid(plaid_record)
    if not matches:
        return []

    doc_ids = [doc.pk for doc, _score in matches]
    docs_by_record = {
        dr: doc
        for doc in DocumentData.objects.filter(associated_record_id__in=doc_ids)
        if (dr := doc.associated_record_id)
    }

    merged: list[Record] = []

    for doc_record, _score in matches:
        document = docs_by_record.get(doc_record.pk)
        result = merge_document_into_plaid(plaid_record, doc_record, document)
        if result is not None:
            merged.append(doc_record)

    return merged


@db_transaction.atomic
def replace_receipt(merge_log: MergeLog, new_document_record: Record) -> Record | None:
    if merge_log.undone_at or not merge_log.plaid_record:
        return None

    plaid_record = merge_log.plaid_record
    old_document_record = merge_log.document_record

    locked_plaid = Record.objects.select_for_update().get(pk=plaid_record.pk)
    new_doc = Record.objects.select_for_update().get(pk=new_document_record.pk)

    if not new_doc.is_active or new_doc.plaid_transaction_id is not None:
        logger.warning(
            "New document record %s is not mergable (is_active=%s, plaid_id=%r)",
            new_document_record.pk,
            new_doc.is_active,
            new_doc.plaid_transaction_id,
        )
        return None

    locked_plaid._skip_auto_match = True
    new_doc._skip_auto_match = True

    pre_plaid_snapshot = _record_snapshot(locked_plaid)

    if old_document_record:
        old_document_record._skip_auto_match = True
        old_document_record.is_active = True
        old_document_record.plaid_transaction_id = None
        old_document_record.save(update_fields=["is_active", "plaid_transaction_id"])

    new_doc_ids = list(
        DocumentData.objects.filter(associated_record=new_doc).values_list("pk", flat=True)
    )
    if new_doc_ids:
        DocumentData.objects.filter(pk__in=new_doc_ids).update(associated_record=locked_plaid)

    if new_doc.products:
        locked_plaid.products = new_doc.products
    if new_doc.notes:
        locked_plaid.notes = new_doc.notes
    if new_doc.record_type != Record.RecordTypes.FINANCIAL_DOCUMENT:
        locked_plaid.record_type = new_doc.record_type
    if new_doc.folder_id:
        locked_plaid.folder_id = new_doc.folder_id

    locked_plaid.payment_method = locked_plaid.payment_method or new_doc.payment_method

    locked_plaid.save(
        update_fields=[
            "products",
            "notes",
            "record_type",
            "folder_id",
            "payment_method",
        ]
    )

    new_doc.is_active = False
    new_doc.save(update_fields=["is_active"])

    merge_log.undone_at = timezone.now()
    merge_log.save(update_fields=["undone_at"])

    new_doc_snapshot = _record_snapshot(new_doc)
    new_doc_snapshot["document_ids"] = new_doc_ids

    MergeLog.objects.create(
        plaid_record=locked_plaid,
        document_record=new_doc,
        document=DocumentData.objects.filter(associated_record=locked_plaid).first(),
        plaid_snapshot=pre_plaid_snapshot,
        document_snapshot=new_doc_snapshot,
    )

    logger.info(
        "Replaced receipt on merge %s: old doc %s, new doc %s",
        merge_log.pk,
        old_document_record.pk if old_document_record else None,
        new_doc.pk,
    )

    return locked_plaid


@db_transaction.atomic
def detach_receipt(merge_log: MergeLog) -> Record | None:
    merge_log = MergeLog.objects.select_for_update().get(pk=merge_log.pk)
    if merge_log.undone_at or not merge_log.plaid_record:
        return None

    plaid_record = merge_log.plaid_record
    document_record = merge_log.document_record

    locked_plaid = Record.objects.select_for_update().get(pk=plaid_record.pk)
    locked_plaid._skip_auto_match = True

    snap = merge_log.plaid_snapshot
    locked_plaid.products = snap.get("products", "")
    locked_plaid.notes = snap.get("notes", "")
    locked_plaid.record_type = snap.get("record_type", Record.RecordTypes.FINANCIAL_DOCUMENT)
    locked_plaid.folder_id = snap.get("folder_id")
    locked_plaid.payment_method = snap.get("payment_method", "")
    locked_plaid.save(
        update_fields=[
            "products",
            "notes",
            "record_type",
            "folder_id",
            "payment_method",
        ]
    )

    if document_record:
        locked_doc = Record.objects.select_for_update().get(pk=document_record.pk)
        locked_doc._skip_auto_match = True
        locked_doc.is_active = True
        locked_doc.plaid_transaction_id = None
        locked_doc.save(update_fields=["is_active", "plaid_transaction_id"])

    doc_ids: list[int] | None = merge_log.document_snapshot.get("document_ids")
    if doc_ids and document_record:
        DocumentData.objects.filter(pk__in=doc_ids).update(associated_record=document_record)

    merge_log.undone_at = timezone.now()
    merge_log.save(update_fields=["undone_at"])

    logger.info("Detached receipt from merge %s", merge_log.pk)

    return locked_plaid
