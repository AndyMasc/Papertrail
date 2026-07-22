from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from records.matching import (
    BALANCE_TOLERANCE,
    DATE_TOLERANCE_DAYS,
    MERGE_SCORE_THRESHOLD,
    _record_snapshot,
    calculate_match_score,
    find_best_plaid_match,
    find_document_matches_for_plaid,
    merge_document_into_plaid,
    try_match_document_record,
    try_match_plaid_record,
    undo_merge,
)
from records.models import MergeLog, Record, Folder

from ._helpers import make_plaid_record, make_doc_record


class CalculateMatchScoreTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="match_score", password="pass")
        self.plaid = make_plaid_record(self.user, "Amazon Purchase")
        self.doc = make_doc_record(self.user, "Amazon Purchase")

    def test_perfect_match(self):
        score = calculate_match_score(self.plaid, self.doc)
        self.assertEqual(score, 120)

    def test_no_match(self):
        doc = make_doc_record(
            self.user,
            "Something Completely Different",
            balance=Decimal("999.99"),
            transaction_date=date(2024, 1, 1),
            merchant="Nowhere",
        )
        score = calculate_match_score(self.plaid, doc)
        self.assertLess(score, MERGE_SCORE_THRESHOLD)

    def test_balance_only_match(self):
        doc = make_doc_record(self.user, "Unrelated", merchant="", transaction_date=None)
        score = calculate_match_score(self.plaid, doc)
        self.assertGreaterEqual(score, 40)
        self.assertLess(score, 50)

    def test_date_only_match(self):
        doc = make_doc_record(self.user, "Unrelated", balance=None, merchant="")
        score = calculate_match_score(self.plaid, doc)
        self.assertGreaterEqual(score, 30)
        self.assertLess(score, 40)

    def test_merchant_partial_match(self):
        doc = make_doc_record(
            self.user, "Something", merchant="amazon", balance=None, transaction_date=None
        )
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 10)

    def test_title_partial_match(self):
        doc = make_doc_record(self.user, "amazon", merchant="", balance=None, transaction_date=None)
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 8)

    def test_balance_within_tolerance(self):
        doc = make_doc_record(self.user, "Amazon Purchase", balance=Decimal("100.50"))
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 110)

    def test_date_one_day_off(self):
        doc = make_doc_record(self.user, "Amazon Purchase", transaction_date=date(2024, 6, 16))
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 110)

    def test_both_none_balance_and_date(self):
        doc = make_doc_record(self.user, "Amazon Purchase", balance=None, transaction_date=None)
        score = calculate_match_score(self.plaid, doc)
        self.assertGreaterEqual(score, 50)

    def test_empty_strings_do_not_raise(self):
        self.plaid.merchant = ""
        self.plaid.title = ""
        doc = make_doc_record(
            self.user, "Something", merchant="", balance=None, transaction_date=None
        )
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 0)


class FindBestPlaidMatchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="best_plaid", password="pass")
        self.plaid = make_plaid_record(self.user, "Target Purchase")
        self.doc = make_doc_record(self.user, "Target Purchase")

    def test_finds_match(self):
        match = find_best_plaid_match(self.doc)
        self.assertIsNotNone(match)
        self.assertEqual(match.pk, self.plaid.pk)

    def test_no_match_below_threshold(self):
        doc = make_doc_record(
            self.user,
            "Completely Different",
            balance=Decimal("999.99"),
            transaction_date=date(2020, 1, 1),
            merchant="Nowhere",
        )
        match = find_best_plaid_match(doc)
        self.assertIsNone(match)

    def test_excludes_own_pk(self):
        other = User.objects.create_user(username="exclude_pk", password="pass")
        plaid = make_plaid_record(other, "Self")
        match = find_best_plaid_match(plaid)
        self.assertIsNone(match)

    def test_user_isolation(self):
        other = User.objects.create_user(username="other_best", password="pass")
        make_plaid_record(other, "Other User Purchase")
        doc = make_doc_record(
            other,
            "Other User Purchase",
            balance=Decimal("100.00"),
            transaction_date=date(2024, 6, 15),
        )
        match = find_best_plaid_match(doc)
        self.assertEqual(match.title, "Other User Purchase")
        self.plaid.delete()
        my_doc = make_doc_record(
            self.user,
            "Other User Purchase",
            balance=Decimal("100.00"),
            transaction_date=date(2024, 6, 15),
        )
        my_match = find_best_plaid_match(my_doc)
        self.assertIsNone(my_match)

    def test_ignores_inactive_plaid(self):
        self.plaid.is_active = False
        self.plaid.save()
        match = find_best_plaid_match(self.doc)
        self.assertIsNone(match)

    def test_best_score_wins(self):
        make_plaid_record(
            self.user, "Worse Match", balance=Decimal("50.00"), transaction_date=date(2024, 1, 1)
        )
        match = find_best_plaid_match(self.doc)
        self.assertEqual(match.pk, self.plaid.pk)


class FindDocumentMatchesForPlaidTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="find_doc", password="pass")
        self.plaid = make_plaid_record(self.user, "Best Buy")

    def test_finds_matching_docs(self):
        doc = make_doc_record(self.user, "Best Buy")
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0].pk, doc.pk)

    def test_returns_empty_when_no_match(self):
        make_doc_record(self.user, "Not Matching", balance=Decimal("999.99"), merchant="Different")
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(matches, [])

    def test_multiple_matches_sorted_by_score(self):
        perfect = make_doc_record(self.user, "Best Buy")
        partial = make_doc_record(
            self.user,
            "Best",
            balance=Decimal("100.50"),
            transaction_date=date(2024, 6, 16),
            merchant="Best Buy",
        )
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0][0].pk, perfect.pk)
        self.assertGreater(matches[0][1], matches[1][1])

    def test_excludes_self(self):
        match = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(match, [])

    def test_user_isolation(self):
        other = User.objects.create_user(username="other_find", password="pass")
        make_doc_record(other, "Best Buy")
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(matches, [])

    def test_ignores_inactive_docs(self):
        make_doc_record(self.user, "Best Buy", is_active=False)
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(matches, [])

    def test_ignores_docs_with_plaid_id(self):
        make_doc_record(self.user, "Best Buy", plaid_transaction_id="already_merged")
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(matches, [])


class MergeDocumentIntoPlaidTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="merge_doc", password="pass")
        self.plaid = make_plaid_record(self.user, "Walmart")
        self.doc = make_doc_record(
            self.user, "Walmart", products="Milk|Eggs", notes="Weekly groceries"
        )
        self.doc_folder = Folder.objects.create(user=self.user, name="Groceries")
        self.plaid_with_folder = make_plaid_record(self.user, "Costco", folder=self.doc_folder)
        self.doc_with_docref = make_doc_record(
            self.user,
            "Receipt DocRef",
            products="Paper|Pens",
        )

    def test_merge_basic(self):
        result = merge_document_into_plaid(self.plaid, self.doc)
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.plaid.pk)

    def test_merge_copies_products_and_notes(self):
        merge_document_into_plaid(self.plaid, self.doc)
        self.plaid.refresh_from_db()
        self.assertEqual(self.plaid.products, "Milk|Eggs")
        self.assertEqual(self.plaid.notes, "Weekly groceries")

    def test_merge_updates_record_type(self):
        self.doc.record_type = Record.RecordTypes.VOUCHER
        self.doc.save()
        merge_document_into_plaid(self.plaid, self.doc)
        self.plaid.refresh_from_db()
        self.assertEqual(self.plaid.record_type, Record.RecordTypes.VOUCHER)

    def test_merge_copies_folder(self):
        doc_with_folder = make_doc_record(
            self.user,
            "Folder Doc",
            folder=self.doc_folder,
        )
        merge_document_into_plaid(self.plaid, doc_with_folder)
        self.plaid.refresh_from_db()
        self.assertEqual(self.plaid.folder, self.doc_folder)

    def test_merge_deactivates_doc(self):
        merge_document_into_plaid(self.plaid, self.doc)
        self.doc.refresh_from_db()
        self.assertFalse(self.doc.is_active)

    def test_merge_creates_log(self):
        merge_document_into_plaid(self.plaid, self.doc)
        log = MergeLog.objects.filter(document_record=self.doc).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.plaid_record, self.plaid)
        self.assertIsNone(log.undone_at)

    def test_merge_moves_all_documents(self):
        import hashlib
        from documents.models import DocumentData

        doc_data1 = DocumentData.objects.create(
            user=self.user,
            associated_record=self.doc_with_docref,
            filepath="users/1/test.pdf",
            file_hash=hashlib.sha256(b"test1").hexdigest(),
        )
        doc_data2 = DocumentData.objects.create(
            user=self.user,
            associated_record=self.doc_with_docref,
            filepath="users/2/other.pdf",
            file_hash=hashlib.sha256(b"test2").hexdigest(),
        )
        merge_document_into_plaid(self.plaid, self.doc_with_docref, doc_data1)
        doc_data1.refresh_from_db()
        doc_data2.refresh_from_db()
        self.assertEqual(doc_data1.associated_record, self.plaid)
        self.assertEqual(doc_data2.associated_record, self.plaid)
        self.assertFalse(
            DocumentData.objects.filter(associated_record=self.doc_with_docref).exists()
        )

    def test_merge_snapshot_tracks_document_ids(self):
        import hashlib
        from documents.models import DocumentData

        doc_data = DocumentData.objects.create(
            user=self.user,
            associated_record=self.doc_with_docref,
            filepath="users/1/test.pdf",
            file_hash=hashlib.sha256(b"test3").hexdigest(),
        )
        merge_document_into_plaid(self.plaid, self.doc_with_docref, doc_data)
        log = MergeLog.objects.filter(document_record=self.doc_with_docref).first()
        self.assertIn(doc_data.pk, log.document_snapshot.get("document_ids", []))

    def test_merge_concurrency_guard_doc_inactive(self):
        self.doc.is_active = False
        self.doc.save()
        result = merge_document_into_plaid(self.plaid, self.doc)
        self.assertIsNone(result)

    def test_merge_concurrency_guard_doc_has_plaid_id(self):
        self.doc.plaid_transaction_id = "already_merged"
        self.doc.save()
        result = merge_document_into_plaid(self.plaid, self.doc)
        self.assertIsNone(result)

    def test_merge_preserves_plaid_type_when_doc_is_financial(self):
        plaid = make_plaid_record(
            self.user,
            "Bank Fee",
            record_type=Record.RecordTypes.EXPENSE_RECEIPT,
        )
        doc = make_doc_record(
            self.user,
            "Bank Fee",
            record_type=Record.RecordTypes.FINANCIAL_DOCUMENT,
        )
        merge_document_into_plaid(plaid, doc)
        plaid.refresh_from_db()
        self.assertEqual(plaid.record_type, Record.RecordTypes.EXPENSE_RECEIPT)


class UndoMergeTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="undo_merge", password="pass")
        self.plaid = make_plaid_record(self.user, "Home Depot")
        self.doc = make_doc_record(
            self.user, "Home Depot", products="Tools", notes="Renovation supplies"
        )
        self.merge_log = MergeLog.objects.create(
            plaid_record=self.plaid,
            document_record=self.doc,
            plaid_snapshot=_record_snapshot(self.plaid),
            document_snapshot=_record_snapshot(self.doc),
        )
        self.plaid.products = "Tools"
        self.plaid.notes = "Renovation supplies"
        self.plaid.save()
        self.doc.is_active = False
        self.doc.save()

    def test_undo_restores_doc_active(self):
        result = undo_merge(self.merge_log)
        self.assertIsNotNone(result)
        self.doc.refresh_from_db()
        self.assertTrue(self.doc.is_active)

    def test_undo_clears_doc_plaid_id(self):
        undo_merge(self.merge_log)
        self.doc.refresh_from_db()
        self.assertIsNone(self.doc.plaid_transaction_id)

    def test_undo_restores_plaid_snapshot(self):
        self.plaid.record_type = Record.RecordTypes.VOUCHER
        self.plaid.products = "Overwritten"
        self.plaid.notes = "Overwritten notes"
        self.plaid.save()
        undo_merge(self.merge_log)
        self.plaid.refresh_from_db()
        self.assertEqual(self.plaid.products, "")
        self.assertNotEqual(self.plaid.products, "Overwritten")
        self.assertNotEqual(self.plaid.notes, "Overwritten notes")

    def test_undo_marks_log_undone(self):
        undo_merge(self.merge_log)
        self.merge_log.refresh_from_db()
        self.assertIsNotNone(self.merge_log.undone_at)

    def test_undo_already_undone_returns_none(self):
        undo_merge(self.merge_log)
        result = undo_merge(self.merge_log)
        self.assertIsNone(result)

    def test_undo_restores_all_documents(self):
        import hashlib
        from documents.models import DocumentData

        doc1 = make_doc_record(self.user, "Doc With Files", products="Items", notes="Important")
        doc_data1 = DocumentData.objects.create(
            user=self.user,
            associated_record=doc1,
            filepath="users/1/file1.pdf",
            file_hash=hashlib.sha256(b"doc1").hexdigest(),
        )
        doc_data2 = DocumentData.objects.create(
            user=self.user,
            associated_record=doc1,
            filepath="users/1/file2.pdf",
            file_hash=hashlib.sha256(b"doc2").hexdigest(),
        )
        plaid = make_plaid_record(self.user, "Doc Restore")
        merge_document_into_plaid(plaid, doc1, doc_data1)
        log = MergeLog.objects.filter(document_record=doc1).first()
        undo_merge(log)
        doc_data1.refresh_from_db()
        doc_data2.refresh_from_db()
        self.assertEqual(doc_data1.associated_record, doc1)
        self.assertEqual(doc_data2.associated_record, doc1)


class TryMatchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="try_match", password="pass")

    def test_try_match_document_record_found(self):
        plaid = make_plaid_record(self.user, "Staples")
        doc = make_doc_record(self.user, "Staples")
        result = try_match_document_record(doc)
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, plaid.pk)

    def test_try_match_document_record_not_found(self):
        doc = make_doc_record(
            self.user,
            "Unique Item No Match",
            balance=Decimal("999.99"),
            transaction_date=date(2020, 1, 1),
            merchant="Nowhere",
        )
        result = try_match_document_record(doc)
        self.assertIsNone(result)

    def test_try_match_plaid_record_found(self):
        plaid = make_plaid_record(self.user, "Office Depot")
        make_doc_record(self.user, "Office Depot")
        merged = try_match_plaid_record(plaid)
        self.assertEqual(len(merged), 1)

    def test_try_match_plaid_record_multiple(self):
        plaid = make_plaid_record(self.user, "Multi Store")
        make_doc_record(self.user, "Multi Store")
        make_doc_record(
            self.user, "Multi Store", balance=Decimal("100.00"), transaction_date=date(2024, 6, 15)
        )
        merged = try_match_plaid_record(plaid)
        self.assertEqual(len(merged), 2)

    def test_try_match_plaid_record_not_found(self):
        plaid = make_plaid_record(self.user, "Solo")
        merged = try_match_plaid_record(plaid)
        self.assertEqual(merged, [])

    def test_try_match_plaid_record_no_double_merge(self):
        plaid = make_plaid_record(self.user, "Single")
        doc = make_doc_record(self.user, "Single")
        try_match_plaid_record(plaid)
        doc.refresh_from_db()
        self.assertFalse(doc.is_active)
        merged_again = try_match_plaid_record(plaid)
        self.assertEqual(merged_again, [])


class MergeLogModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="merge_log_model", password="pass")
        self.plaid = make_plaid_record(self.user, "Model Test")
        self.doc = make_doc_record(self.user, "Model Test")
        self.log = MergeLog.objects.create(
            plaid_record=self.plaid,
            document_record=self.doc,
            plaid_snapshot={},
            document_snapshot={},
        )

    def test_str_representation(self):
        expected = f"Merge {self.log.pk}: plaid={self.plaid.pk} <- doc={self.doc.pk}"
        self.assertEqual(str(self.log), expected)

    def test_default_ordering(self):
        other_plaid = make_plaid_record(self.user, "Order Plaid")
        other_doc = make_doc_record(self.user, "Order Doc")
        log2 = MergeLog.objects.create(
            plaid_record=other_plaid,
            document_record=other_doc,
            plaid_snapshot={},
            document_snapshot={},
        )
        qs = MergeLog.objects.all()
        self.assertEqual(qs.first(), log2)

    def test_null_fks_allowed(self):
        log = MergeLog.objects.create(
            plaid_snapshot={},
            document_snapshot={},
        )
        self.assertIsNone(log.plaid_record)
        self.assertIsNone(log.document_record)


class AutoMatchSignalTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="signal_test", password="pass")

    def _assert_on_commit_called(self, record_save_fn):
        with patch("records.signals.transaction.on_commit") as mock_cb:
            record_save_fn()
            mock_cb.assert_called_once()

    def _assert_on_commit_not_called(self, record_save_fn):
        with patch("records.signals.transaction.on_commit") as mock_cb:
            record_save_fn()
            mock_cb.assert_not_called()

    def test_skip_on_create(self):
        self._assert_on_commit_not_called(
            lambda: Record.objects.create(
                user=self.user, title="Test", record_type="expense_receipt"
            )
        )

    def test_skip_on_inactive(self):
        record = Record.objects.create(user=self.user, title="Test", record_type="expense_receipt")
        record.is_active = False
        self._assert_on_commit_not_called(lambda: record.save())

    def test_skip_on_skip_flag(self):
        record = Record.objects.create(user=self.user, title="Test", record_type="expense_receipt")
        record._skip_auto_match = True
        self._assert_on_commit_not_called(lambda: record.save())

    def test_runs_on_update(self):
        record = Record.objects.create(user=self.user, title="Test", record_type="expense_receipt")
        record.title = "Updated"
        self._assert_on_commit_called(lambda: record.save())

    def test_runs_on_plaid_record_update(self):
        plaid = make_plaid_record(self.user, "Signal Plaid")
        plaid.title = "Updated Plaid"
        self._assert_on_commit_called(lambda: plaid.save())
