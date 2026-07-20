import difflib
import logging
from decimal import Decimal
from typing import Any

from django.db import transaction as db_transaction
from django.utils import timezone

from documents.models import DocumentData
from records.models import MergeLog, Record

logger = logging.getLogger(__name__)

BALANCE_TOLERANCE = Decimal("1.00")
DATE_TOLERANCE_DAYS = 3
MERGE_SCORE_THRESHOLD = 55


def _normalize(text: str) -> str:
    return text.lower().strip()


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


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


def find_best_plaid_match(record: Record) -> Record | None:
    candidates = Record.objects.filter(
        user=record.user,
        plaid_transaction_id__isnull=False,
        is_active=True,
    ).exclude(pk=record.pk)

    best_score = 0
    best_match: Record | None = None

    for candidate in candidates.iterator(chunk_size=500):
        score = calculate_match_score(record, candidate)
        if score > best_score:
            best_score = score
            best_match = candidate

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
    doc_records = Record.objects.filter(
        user=plaid_record.user,
        plaid_transaction_id__isnull=True,
        is_active=True,
    ).exclude(pk=plaid_record.pk)

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
    }


@db_transaction.atomic
def merge_document_into_plaid(
    plaid_record: Record,
    document_record: Record,
    document: DocumentData | None = None,
) -> Record | None:
    locked_plaid = Record.objects.select_for_update().get(pk=plaid_record.pk)

    fresh_doc = Record.objects.get(pk=document_record.pk)
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

    if fresh_doc.products:
        locked_plaid.products = fresh_doc.products
    if fresh_doc.notes:
        locked_plaid.notes = fresh_doc.notes
    if fresh_doc.record_type != Record.RecordTypes.FINANCIAL_DOCUMENT:
        locked_plaid.record_type = fresh_doc.record_type
    if fresh_doc.folder_id:
        locked_plaid.folder_id = fresh_doc.folder_id

    locked_plaid.save()

    if document:
        document.associated_record = locked_plaid
        document.save(update_fields=["associated_record"])

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
    if merge_log.undone_at:
        return None

    plaid_record = merge_log.plaid_record
    document_record = merge_log.document_record
    document = merge_log.document

    if plaid_record and plaid_record.is_active:
        plaid_record._skip_auto_match = True
        snap = merge_log.plaid_snapshot
        plaid_record.products = snap.get("products", "")
        plaid_record.notes = snap.get("notes", "")
        plaid_record.record_type = snap.get("record_type", Record.RecordTypes.FINANCIAL_DOCUMENT)
        plaid_record.folder_id = snap.get("folder_id")
        plaid_record.save()

    if document_record:
        document_record._skip_auto_match = True
        document_record.is_active = True
        document_record.plaid_transaction_id = None
        document_record.save(update_fields=["is_active", "plaid_transaction_id"])

    if document and document_record:
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
    merged: list[Record] = []

    for doc_record, _score in matches:
        document = DocumentData.objects.filter(associated_record=doc_record).first()
        result = merge_document_into_plaid(plaid_record, doc_record, document)
        if result is not None:
            merged.append(doc_record)

    return merged
