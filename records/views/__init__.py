"""Public view classes exposed by the records views package.

Re-exports every view from the sub-modules so they can be imported
directly as ``records.views.RecordListView`` etc.
"""

from .create import AddRecordView, CheckOCRStatus
from .folders import CreateFolder, FolderDeleteView, FolderListView, FolderUpdateView
from .history import RecordHistoryView
from .merges import (
    ManualMergeModalView,
    ManualMergeSearchView,
    ManualMergeView,
    MergeListView,
    UndoMergeView,
)
from .record_state import ArchiveRecord, DeleteRecordView, UnarchiveRecord
from .records import HardDeleteRecordView, RecordDetailView, RecordListView

__all__ = [
    "RecordListView",
    "RecordDetailView",
    "RecordHistoryView",
    "AddRecordView",
    "CheckOCRStatus",
    "HardDeleteRecordView",
    "ArchiveRecord",
    "UnarchiveRecord",
    "DeleteRecordView",
    "FolderListView",
    "CreateFolder",
    "FolderUpdateView",
    "FolderDeleteView",
    "ManualMergeView",
    "ManualMergeSearchView",
    "ManualMergeModalView",
    "MergeListView",
    "UndoMergeView",
]
