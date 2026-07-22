"""Shared type definitions for the records module.

Provides TypedDicts and dataclasses that describe the shape of data
exchanged between merge logic, views, and templates, independent of
Django model instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NotRequired, TypedDict


class RecordSnapshot(TypedDict):
    """Snapshot of a Record's editable fields, stored in MergeLog as JSON.

    Captures the state of a bank transaction record before a merge modifies it,
    so the merge can be undone or the receipt detached later.
    """

    products: str
    notes: str
    record_type: str
    folder_id: int | None
    is_active: bool
    plaid_transaction_id: str | None
    title: str
    merchant: str
    balance: str | None
    transaction_date: str | None
    payment_method: str
    document_ids: NotRequired[list[int]]


@dataclass
class HistoryEntry:
    """Unified history entry that normalizes django-simple-history and MergeLog into one timeline.

    RecordHistoryView merges three data sources (Record history, DocumentData history,
    MergeLog) into a single chronological list. This dataclass provides a consistent
    shape for template rendering instead of monkey-patching SimpleNamespace onto objects.
    """

    source_type: str
    history_type: str
    history_date: Any
    history_user: Any = None
    merge: Any = None
    instance: Any = None
    changed_fields: dict[str, Any] = field(default_factory=dict)
