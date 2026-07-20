from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from records.filters import RecordFilter
from records.forms import AddRecordForm, RecordUpdateForm, FolderForm
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


class RecordModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.record = Record.objects.create(
            user=self.user,
            title="Test Record",
            record_type="expense_receipt",
        )

    def test_str(self):
        self.assertEqual(str(self.record), "Test Record")

    def test_badge_classes_by_record_type(self):
        classes = self.record.badge_classes
        self.assertIn("bg-emerald", classes)
        self.assertIn("text-emerald", classes)

    def test_badge_classes_other_type(self):
        self.record.record_type = "voucher"
        self.record.save()
        classes = self.record.badge_classes
        self.assertIn("bg-amber", classes)

    def test_is_expired(self):
        self.record.expiry_date = timezone.now().date() - timedelta(days=1)
        self.record.save()
        self.assertTrue(self.record.is_expired)

    def test_is_not_expired_no_expiry(self):
        self.assertFalse(self.record.is_expired)

    def test_is_not_expired_future(self):
        self.record.expiry_date = timezone.now().date() + timedelta(days=1)
        self.record.save()
        self.assertFalse(self.record.is_expired)

    def test_is_expiring_soon_30_days(self):
        self.record.expiry_date = timezone.now().date() + timedelta(days=29)
        self.record.save()
        self.assertTrue(self.record.is_expiring_soon)

    def test_is_not_expiring_soon_beyond_30(self):
        self.record.expiry_date = timezone.now().date() + timedelta(days=31)
        self.record.save()
        self.assertFalse(self.record.is_expiring_soon)

    def test_is_not_expiring_soon_no_expiry(self):
        self.assertFalse(self.record.is_expiring_soon)

    def test_date_added_auto_now(self):
        self.assertIsNotNone(self.record.date_added)
        self.assertIsNotNone(self.record.last_edited)

    def test_is_active_default(self):
        self.assertTrue(self.record.is_active)

    def test_ordering_by_last_edited_desc(self):
        r1 = Record.objects.create(user=self.user, title="First", record_type="expense_receipt")
        r2 = Record.objects.create(user=self.user, title="Second", record_type="voucher")
        qs = Record.objects.all()
        self.assertEqual(qs.first(), r2)

    def test_default_balance(self):
        self.assertIsNone(self.record.balance)

    def test_default_strings(self):
        self.assertEqual(self.record.merchant, "")
        self.assertEqual(self.record.products, "")
        self.assertEqual(self.record.notes, "")

    def test_default_record_type(self):
        record = Record.objects.create(user=self.user, title="Default Type")
        self.assertEqual(record.record_type, "expense_receipt")

    def test_queryset_for_user(self):
        user2 = User.objects.create_user(username="user2", password="pass")
        Record.objects.create(user=user2, title="Other", record_type="expense_receipt")
        self.assertEqual(Record.objects.for_user(self.user).count(), 1)
        self.assertEqual(Record.objects.for_user(user2).count(), 1)

    def test_queryset_active(self):
        Record.objects.create(
            user=self.user,
            title="Inactive",
            record_type="voucher",
            is_active=False,
        )
        qs = Record.objects.active()
        self.assertEqual(qs.count(), 1)

    def test_queryset_archived_inactive(self):
        inactive = Record.objects.create(
            user=self.user,
            title="Inactive",
            record_type="voucher",
            is_active=False,
        )
        qs = Record.objects.archived()
        self.assertIn(inactive, qs)
        self.assertNotIn(self.record, qs)

    def test_queryset_with_documents(self):
        from documents.models import DocumentData
        import hashlib

        DocumentData.objects.create(
            user=self.user,
            associated_record=self.record,
            filepath="users/1/doc.pdf",
            file_hash=hashlib.sha256(b"doc").hexdigest(),
        )
        qs = Record.objects.with_documents()
        self.assertIn(self.record, qs)

    def test_queryset_expiring_soon(self):
        self.record.expiry_date = timezone.now().date() + timedelta(days=7)
        self.record.save()
        qs = Record.objects.expiring_soon()
        self.assertIn(self.record, qs)

    def test_queryset_expiring_soon_default_30_days(self):
        self.record.expiry_date = timezone.now().date() + timedelta(days=25)
        self.record.save()
        qs = Record.objects.expiring_soon()
        self.assertIn(self.record, qs)

    def test_queryset_expiring_soon_excludes_beyond_30(self):
        self.record.expiry_date = timezone.now().date() + timedelta(days=35)
        self.record.save()
        qs = Record.objects.expiring_soon()
        self.assertNotIn(self.record, qs)

    def test_queryset_expired(self):
        self.record.expiry_date = timezone.now().date() - timedelta(days=1)
        self.record.save()
        qs = Record.objects.expired()
        self.assertIn(self.record, qs)

    def test_queryset_expired_excludes_future(self):
        self.record.expiry_date = timezone.now().date() + timedelta(days=1)
        self.record.save()
        qs = Record.objects.expired()
        self.assertNotIn(self.record, qs)

    def test_expired_excludes_inactive(self):
        self.record.expiry_date = timezone.now().date() - timedelta(days=1)
        self.record.is_active = False
        self.record.save()
        qs = Record.objects.expired()
        self.assertNotIn(self.record, qs)

    def test_smart_search_title(self):
        Record.objects.create(
            user=self.user,
            title="Tax Return 2024",
            record_type="tax_document",
        )
        qs = Record.objects.smart_search("Tax Return")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_merchant(self):
        Record.objects.create(
            user=self.user,
            title="Purchase",
            merchant="Amazon",
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("Amazon")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_products(self):
        Record.objects.create(
            user=self.user,
            title="Grocery",
            products="Milk|Eggs|Bread",
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("Eggs")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_notes(self):
        Record.objects.create(
            user=self.user,
            title="Note",
            notes="Important document",
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("Important")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_record_type(self):
        Record.objects.create(
            user=self.user,
            title="Warranty Doc",
            record_type="warranty_certificate",
        )
        qs = Record.objects.smart_search("Warranty")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_balance(self):
        Record.objects.create(
            user=self.user,
            title="Expense",
            balance=Decimal("150.00"),
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("150")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_balance_not_matching(self):
        Record.objects.create(
            user=self.user,
            title="Expense",
            balance=Decimal("200.00"),
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("150")
        self.assertEqual(qs.count(), 0)

    def test_smart_search_isodate(self):
        Record.objects.create(
            user=self.user,
            title="Dated",
            transaction_date=date(2024, 6, 15),
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("2024-06-15")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_year(self):
        Record.objects.create(
            user=self.user,
            title="This Year",
            transaction_date=date(2024, 6, 15),
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("2024")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_with_setup_record_isolation(self):
        if self.record.title == "Test Record":
            self.assertEqual(Record.objects.count(), 1)

    def test_smart_search_month_name(self):
        today = timezone.now().date()
        Record.objects.create(
            user=self.user,
            title="June Purchase",
            transaction_date=date(today.year, 6, 15),
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("june")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_relative_today(self):
        Record.objects.create(
            user=self.user,
            title="Today",
            transaction_date=timezone.now().date(),
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("today")
        self.assertGreaterEqual(qs.count(), 1)

    def test_smart_search_relative_yesterday(self):
        Record.objects.create(
            user=self.user,
            title="Yesterday",
            transaction_date=timezone.now().date() - timedelta(days=1),
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("yesterday")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_relative_tomorrow(self):
        Record.objects.create(
            user=self.user,
            title="Tomorrow Record",
            transaction_date=timezone.now().date() + timedelta(days=1),
            record_type="expense_receipt",
        )
        qs = Record.objects.smart_search("tomorrow")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_empty_returns_all(self):
        qs = Record.objects.smart_search("")
        self.assertEqual(qs.count(), 1)

    def test_smart_search_no_match(self):
        qs = Record.objects.smart_search("zzzznotfound")
        self.assertEqual(qs.count(), 0)

    def test_smart_search_year_trumps_month(self):
        self.record.transaction_date = date(2024, 3, 15)
        self.record.save()
        qs = Record.objects.smart_search(f"{timezone.now().date().year}")
        self.assertEqual(qs.count(), 1)


class RecordModelExpiryNotificationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")

    def test_expiry_notification_sent_default(self):
        record = Record.objects.create(user=self.user, title="Test", record_type="expense_receipt")
        self.assertFalse(record.expiry_notification_sent)


class FolderModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="folderuser", password="pass")

    def test_create_folder(self):
        folder = Folder.objects.create(user=self.user, name="Tax Documents")
        self.assertEqual(str(folder), "Tax Documents")
        self.assertIsNotNone(folder.created_at)

    def test_folder_default_no_color_field(self):
        folder = Folder.objects.create(user=self.user, name="Default")
        self.assertFalse(hasattr(folder, "color"))


class FolderFormTest(TestCase):
    def test_valid_data(self):
        form = FolderForm(data={"name": "New Folder"})
        self.assertTrue(form.is_valid())

    def test_blank_name(self):
        form = FolderForm(data={"name": ""})
        self.assertFalse(form.is_valid())

    def test_max_length(self):
        form = FolderForm(data={"name": "A" * 256})
        self.assertFalse(form.is_valid())

    def test_no_color_field(self):
        form = FolderForm()
        self.assertNotIn("color", form.fields)


class BaseRecordFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="formuser", password="pass")

    def test_required_fields(self):
        form = AddRecordForm(user=self.user, data={})
        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)
        self.assertIn("record_type", form.errors)

    def test_minimal_valid(self):
        form = AddRecordForm(
            user=self.user,
            data={
                "title": "Test",
                "products": "Item",
                "record_type": "expense_receipt",
            },
        )
        self.assertTrue(form.is_valid())

    def test_balance_negative(self):
        form = AddRecordForm(
            user=self.user,
            data={
                "title": "Test",
                "products": "Item",
                "record_type": "expense_receipt",
                "balance": "-10.00",
            },
        )
        self.assertFalse(form.is_valid())

    def test_balance_valid(self):
        form = AddRecordForm(
            user=self.user,
            data={
                "title": "Test",
                "products": "Item",
                "record_type": "expense_receipt",
                "balance": "150.50",
            },
        )
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["balance"], Decimal("150.50"))

    def test_transaction_date_future(self):
        future = (timezone.now().date() + timedelta(days=5)).isoformat()
        form = AddRecordForm(
            user=self.user,
            data={
                "title": "Test",
                "products": "Item",
                "record_type": "expense_receipt",
                "transaction_date": future,
            },
        )
        self.assertFalse(form.is_valid())

    def test_transaction_date_past_valid(self):
        past = (timezone.now().date() - timedelta(days=5)).isoformat()
        form = AddRecordForm(
            user=self.user,
            data={
                "title": "Test",
                "products": "Item",
                "record_type": "expense_receipt",
                "transaction_date": past,
            },
        )
        self.assertTrue(form.is_valid())

    def test_expiry_before_transaction(self):
        form = AddRecordForm(
            user=self.user,
            data={
                "title": "Test",
                "products": "Item",
                "record_type": "expense_receipt",
                "transaction_date": "2024-06-15",
                "expiry_date": "2024-06-14",
            },
        )
        self.assertFalse(form.is_valid())

    def test_expiry_equal_to_transaction(self):
        form = AddRecordForm(
            user=self.user,
            data={
                "title": "Test",
                "products": "Item",
                "record_type": "expense_receipt",
                "transaction_date": "2024-06-15",
                "expiry_date": "2024-06-15",
            },
        )
        self.assertTrue(form.is_valid())

    def test_expiry_after_transaction(self):
        form = AddRecordForm(
            user=self.user,
            data={
                "title": "Test",
                "products": "Item",
                "record_type": "expense_receipt",
                "transaction_date": "2024-06-15",
                "expiry_date": "2024-06-20",
            },
        )
        self.assertTrue(form.is_valid())

    def test_expiry_without_transaction_date(self):
        form = AddRecordForm(
            user=self.user,
            data={
                "title": "Test",
                "products": "Item",
                "record_type": "expense_receipt",
                "expiry_date": "2024-06-20",
            },
        )
        self.assertTrue(form.is_valid())

    def test_balance_none(self):
        form = AddRecordForm(
            user=self.user,
            data={
                "title": "Test",
                "products": "Item",
                "record_type": "expense_receipt",
            },
        )
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["balance"])

    def test_folder_queryset_filtered_by_user(self):
        folder = Folder.objects.create(user=self.user, name="My Folder")
        form = AddRecordForm(user=self.user)
        self.assertIn(folder, form.fields["folder"].queryset)


class AddRecordFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="formuser", password="pass")

    def test_form_fields(self):
        form = AddRecordForm(user=self.user)
        expected = [
            "title",
            "products",
            "merchant",
            "balance",
            "transaction_date",
            "expiry_date",
            "record_type",
            "notes",
            "folder",
        ]
        self.assertEqual(list(form.fields.keys()), expected)


class RecordUpdateFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="formuser", password="pass")

    def test_fields_match(self):
        update_fields = set(RecordUpdateForm().fields.keys())
        add_fields = set(AddRecordForm(user=self.user).fields.keys())
        self.assertEqual(update_fields, add_fields)


def _make_filter_request(user):
    from django.http import HttpRequest

    req = HttpRequest()
    req.user = user
    return req


class RecordFilterTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="filteruser", password="pass")
        self.active = Record.objects.create(
            user=self.user,
            title="Active Record",
            record_type="expense_receipt",
        )
        self.inactive = Record.objects.create(
            user=self.user,
            title="Inactive Record",
            record_type="voucher",
            is_active=False,
        )
        self.with_expiry = Record.objects.create(
            user=self.user,
            title="Has Expiry",
            record_type="warranty_certificate",
            expiry_date=timezone.now().date() + timedelta(days=3),
        )

    def test_filter_no_params(self):
        f = RecordFilter(
            {},
            queryset=Record.objects.for_user(self.user),
            request=_make_filter_request(self.user),
        )
        self.assertEqual(f.qs.count(), 3)

    def test_filter_is_active_true(self):
        f = RecordFilter(
            {"is_active": "true"},
            queryset=Record.objects.for_user(self.user),
            request=_make_filter_request(self.user),
        )
        self.assertIn(self.active, f.qs)
        self.assertNotIn(self.inactive, f.qs)

    def test_filter_is_active_false(self):
        f = RecordFilter(
            {"is_active": "false"},
            queryset=Record.objects.for_user(self.user),
            request=_make_filter_request(self.user),
        )
        self.assertIn(self.inactive, f.qs)
        self.assertNotIn(self.active, f.qs)

    def test_filter_expiring_soon(self):
        f = RecordFilter(
            {"expiring_soon": True},
            queryset=Record.objects.for_user(self.user),
            request=_make_filter_request(self.user),
        )
        self.assertIn(self.with_expiry, f.qs)

    def test_filter_record_type(self):
        Record.objects.create(
            user=self.user,
            title="Active Voucher",
            record_type="voucher",
        )
        f = RecordFilter(
            {"record_type": "voucher"},
            queryset=Record.objects.for_user(self.user),
            request=_make_filter_request(self.user),
        )
        count = f.qs.count()
        self.assertGreaterEqual(count, 1)

    def test_filter_folder_direct_method(self):
        folder = Folder.objects.create(user=self.user, name="Folder 1")
        self.active.folder = folder
        self.active.save()
        f = RecordFilter(
            {"folder": str(folder.id)},
            queryset=Record.objects.for_user(self.user),
            request=_make_filter_request(self.user),
        )
        result = f.filter_by_folder(Record.objects.for_user(self.user), "folder", str(folder.id))
        self.assertIn(self.active, result)
        self.assertNotIn(self.inactive, result)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class RecordListViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listuser", password="pass")
        self.url = reverse("records:view_all_records")

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_authenticated_access(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "records/record_list_view.html")

    def test_context_has_filter(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertIn("filter", response.context)

    def test_only_own_records_shown(self):
        user2 = User.objects.create_user(username="otherlist", password="pass")
        Record.objects.create(user=user2, title="Other's", record_type="expense_receipt")
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["records"]), 0)

    def test_records_visible(self):
        self.client.force_login(self.user)
        Record.objects.create(user=self.user, title="My Record", record_type="expense_receipt")
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["records"]), 1)

    def test_filter_by_search_query(self):
        self.client.force_login(self.user)
        Record.objects.create(
            user=self.user,
            title="UniqueWidget",
            record_type="expense_receipt",
        )
        response = self.client.get(self.url, {"search": "UniqueWidget"})
        self.assertEqual(len(response.context["records"]), 1)

    def test_filter_no_match(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url, {"search": "NOMATCH"})
        self.assertEqual(len(response.context["records"]), 0)

    def test_pagination_first_page(self):
        self.client.force_login(self.user)
        for i in range(30):
            Record.objects.create(
                user=self.user,
                title=f"Record {i}",
                record_type="expense_receipt",
            )
        response = self.client.get(self.url)
        self.assertTrue(response.context["is_paginated"])
        self.assertEqual(len(response.context["records"]), 25)

    def test_pagination_second_page(self):
        self.client.force_login(self.user)
        for i in range(30):
            Record.objects.create(
                user=self.user,
                title=f"Record {i}",
                record_type="expense_receipt",
            )
        response = self.client.get(self.url, {"page": 2})
        self.assertEqual(len(response.context["records"]), 5)

    def test_pagination_invalid_page_returns_404(self):
        self.client.force_login(self.user)
        Record.objects.create(user=self.user, title="Test", record_type="expense_receipt")
        response = self.client.get(self.url, {"page": "abc"})
        self.assertEqual(response.status_code, 404)

    def test_pagination_out_of_range_page_returns_404(self):
        self.client.force_login(self.user)
        for i in range(30):
            Record.objects.create(
                user=self.user,
                title=f"Record {i}",
                record_type="expense_receipt",
            )
        response = self.client.get(self.url, {"page": 999})
        self.assertEqual(response.status_code, 404)

    def test_pagination_empty_page_returns_no_records(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertFalse(response.context["is_paginated"])
        self.assertEqual(len(response.context["records"]), 0)


class AddRecordViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="adduser", password="pass")
        self.url = reverse("records:add_record_manual")

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_form(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "records/add_record.html")

    def test_post_valid(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {
                "title": "New Record",
                "products": "Test Item",
                "record_type": "expense_receipt",
            },
        )
        self.assertIn(response.status_code, [200, 302])
        self.assertTrue(Record.objects.filter(title="New Record", user=self.user).exists())

    def test_post_invalid(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {"title": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "records/add_record.html")

    def test_post_with_expiry(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {
                "title": "With Expiry",
                "products": "Item",
                "record_type": "warranty_certificate",
                "transaction_date": "2024-01-01",
                "expiry_date": "2024-12-31",
            },
        )
        self.assertIn(response.status_code, [200, 302])
        record = Record.objects.get(title="With Expiry")
        self.assertEqual(record.expiry_date, date(2024, 12, 31))

    def test_post_with_folder(self):
        self.client.force_login(self.user)
        folder = Folder.objects.create(user=self.user, name="Test Folder")
        response = self.client.post(
            self.url,
            {
                "title": "In Folder",
                "products": "Item",
                "record_type": "expense_receipt",
                "folder": folder.id,
            },
        )
        self.assertIn(response.status_code, [200, 302])
        record = Record.objects.get(title="In Folder")
        self.assertEqual(record.folder, folder)

    def test_post_with_balance(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {
                "title": "With Balance",
                "products": "Item",
                "record_type": "expense_receipt",
                "balance": "250.00",
            },
        )
        self.assertIn(response.status_code, [200, 302])
        record = Record.objects.get(title="With Balance")
        self.assertEqual(record.balance, Decimal("250.00"))


class RecordDetailViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="detailuser", password="pass")
        self.record = Record.objects.create(
            user=self.user, title="Detail View", record_type="expense_receipt"
        )
        self.url = reverse("records:record_detail", args=[self.record.id])

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_owner_can_view(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "records/record_detail_view.html")
        self.assertEqual(response.context["record"], self.record)

    def test_other_user_cannot_view(self):
        user2 = User.objects.create_user(username="otherdet", password="pass")
        self.client.force_login(user2)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_record(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("records:record_detail", args=[99999]))
        self.assertEqual(response.status_code, 404)

    def test_update_via_post_with_hx(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {
                "title": "Updated Title",
                "products": "Updated Item",
                "record_type": "voucher",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertIn(response.status_code, [200, 204])
        self.record.refresh_from_db()
        self.assertEqual(self.record.title, "Updated Title")
        self.assertEqual(self.record.record_type, "voucher")

    def test_other_user_cannot_update(self):
        user2 = User.objects.create_user(username="otherupd", password="pass")
        self.client.force_login(user2)
        response = self.client.post(
            self.url,
            {
                "title": "Hacked Title",
                "products": "Item",
                "record_type": "expense_receipt",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.record.refresh_from_db()
        self.assertEqual(self.record.title, "Detail View")


class ArchiveRecordViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="archuser", password="pass")
        self.record = Record.objects.create(
            user=self.user, title="To Archive", record_type="expense_receipt"
        )
        self.url = reverse("records:archive_record", args=[self.record.id])

    def test_login_required(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)

    def test_owner_can_archive(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url)
        self.assertIn(response.status_code, [200, 302])
        self.record.refresh_from_db()
        self.assertFalse(self.record.is_active)

    def test_other_user_cannot_archive(self):
        user2 = User.objects.create_user(username="otherarch", password="pass")
        self.client.force_login(user2)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)


class UnarchiveRecordViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="unarchuser", password="pass")
        self.record = Record.objects.create(
            user=self.user,
            title="To Unarchive",
            record_type="expense_receipt",
            is_active=False,
        )
        self.url = reverse("records:unarchive_record", args=[self.record.id])

    def test_owner_can_unarchive(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url)
        self.assertIn(response.status_code, [200, 302])
        self.record.refresh_from_db()
        self.assertTrue(self.record.is_active)

    def test_other_user_cannot_unarchive(self):
        user2 = User.objects.create_user(username="otherunarch", password="pass")
        self.client.force_login(user2)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)
        self.record.refresh_from_db()
        self.assertFalse(self.record.is_active)

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)


class DeleteRecordViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="deluser", password="pass")
        self.record = Record.objects.create(
            user=self.user, title="To Delete", record_type="expense_receipt"
        )
        self.url = reverse("records:delete_record", args=[self.record.id])

    def test_owner_can_delete(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url)
        self.assertIn(response.status_code, [200, 302])
        self.assertFalse(Record.objects.filter(id=self.record.id).exists())

    def test_other_user_cannot_delete(self):
        user2 = User.objects.create_user(username="otherdel", password="pass")
        self.client.force_login(user2)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Record.objects.filter(id=self.record.id).exists())


class FolderListViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="folderlist", password="pass")
        self.url = reverse("records:view_folders")

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_authenticated_access(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "records/folders.html")

    def test_context_has_folders(self):
        self.client.force_login(self.user)
        Folder.objects.create(user=self.user, name="My Folder")
        response = self.client.get(self.url)
        self.assertIn("folders", response.context)

    def test_only_user_folders_shown(self):
        user2 = User.objects.create_user(username="otherfl", password="pass")
        Folder.objects.create(user=user2, name="Other's")
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["folders"]), 0)


class CreateFolderViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="createf", password="pass")
        self.url = reverse("records:create_folder")

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_form(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "records/partials/create_folder_modal.html")

    def test_post_valid(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {"name": "New Folder"}, HTTP_HX_REQUEST="true")
        self.assertIn(response.status_code, [200, 302])
        self.assertTrue(Folder.objects.filter(name="New Folder", user=self.user).exists())


class FolderUpdateViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="updf", password="pass")
        self.folder = Folder.objects.create(user=self.user, name="Old Name")
        self.url = reverse("records:edit_folder", args=[self.folder.id])

    def test_owner_can_update(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {"name": "New Name"}, HTTP_HX_REQUEST="true")
        self.assertIn(response.status_code, [200, 302])
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, "New Name")

    def test_other_user_cannot_update(self):
        user2 = User.objects.create_user(username="otherupf", password="pass")
        self.client.force_login(user2)
        response = self.client.post(self.url, {"name": "Hacked"})
        self.assertEqual(response.status_code, 404)
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, "Old Name")

    def test_get_method(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)


class FolderDeleteViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="delf", password="pass")
        self.folder = Folder.objects.create(user=self.user, name="To Delete")
        self.url = reverse("records:delete_folder", args=[self.folder.id])

    def test_owner_can_delete(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url)
        self.assertIn(response.status_code, [200, 302])
        self.assertFalse(Folder.objects.filter(id=self.folder.id).exists())

    def test_other_user_cannot_delete(self):
        user2 = User.objects.create_user(username="otherdelf", password="pass")
        self.client.force_login(user2)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Folder.objects.filter(id=self.folder.id).exists())

    def test_delete_removes_folder_from_records(self):
        self.client.force_login(self.user)
        record = Record.objects.create(
            user=self.user,
            title="Folder Record",
            record_type="expense_receipt",
            folder=self.folder,
        )
        record_id = record.id
        response = self.client.post(self.url, HTTP_HX_REQUEST="true")
        self.assertFalse(Folder.objects.filter(id=self.folder.id).exists())
        remaining = Record.objects.filter(id=record_id).exists()
        if remaining:
            record.refresh_from_db()
            self.assertIsNone(record.folder)


class TasksTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="taskuser", password="pass")

    def test_archive_expired_records(self):
        past = timezone.now().date() - timedelta(days=10)
        expired = Record.objects.create(
            user=self.user,
            title="Expired",
            record_type="expense_receipt",
            expiry_date=past,
        )
        Record.objects.filter(pk=expired.pk).update(date_added=past - timedelta(days=1))
        from records.tasks import archive_expired_records

        archive_expired_records()
        expired.refresh_from_db()
        self.assertFalse(expired.is_active)

    def test_archive_expired_records_active_only(self):
        past = timezone.now().date() - timedelta(days=10)
        already_inactive = Record.objects.create(
            user=self.user,
            title="Already Inactive",
            record_type="expense_receipt",
            expiry_date=past,
            is_active=False,
        )
        Record.objects.filter(pk=already_inactive.pk).update(date_added=past - timedelta(days=1))
        from records.tasks import archive_expired_records

        archive_expired_records()
        already_inactive.refresh_from_db()
        self.assertFalse(already_inactive.is_active)

    def test_archive_expired_records_settings_disabled(self):
        self.user.settings.auto_archive_expired_records = False
        self.user.settings.save()
        past = timezone.now().date() - timedelta(days=10)
        expired = Record.objects.create(
            user=self.user,
            title="No Auto",
            record_type="expense_receipt",
            expiry_date=past,
        )
        Record.objects.filter(pk=expired.pk).update(date_added=past - timedelta(days=1))
        from records.tasks import archive_expired_records

        archive_expired_records()
        expired.refresh_from_db()
        self.assertTrue(expired.is_active)

    def test_delete_old_archived(self):
        past = timezone.now().date() - timedelta(days=70)
        old = Record.objects.create(
            user=self.user,
            title="Old Inactive",
            record_type="expense_receipt",
            is_active=False,
            expiry_date=past,
        )
        Record.objects.filter(pk=old.pk).update(
            date_added=past - timedelta(days=80),
            last_edited=timezone.now() - timedelta(days=70),
        )
        from records.tasks import delete_2month_archived_records

        delete_2month_archived_records()
        self.assertFalse(Record.objects.filter(id=old.id).exists())

    def test_delete_old_archived_recent(self):
        recent = Record.objects.create(
            user=self.user,
            title="Recent Inactive",
            record_type="expense_receipt",
            is_active=False,
        )
        from records.tasks import delete_2month_archived_records

        delete_2month_archived_records()
        self.assertTrue(Record.objects.filter(id=recent.id).exists())

    def test_send_expiry_notifications(self):
        future = timezone.now().date() + timedelta(days=3)
        Record.objects.create(
            user=self.user,
            title="Expiring Soon",
            record_type="warranty_certificate",
            expiry_date=future,
        )
        self.user.settings.enable_email_notifications = True
        self.user.settings.enable_push_notifications = True
        self.user.settings.expiring_notifications_advance_time = "7"
        self.user.settings.save()
        from records.tasks import send_expiry_notifications

        result = send_expiry_notifications()
        self.assertIsNone(result)

    def test_send_expiry_notifications_no_expiring(self):
        from records.tasks import send_expiry_notifications

        result = send_expiry_notifications()
        self.assertIsNone(result)

    def test_send_expiry_notifications_user_disabled(self):
        future = timezone.now().date() + timedelta(days=3)
        Record.objects.create(
            user=self.user,
            title="Expiring Soon",
            record_type="warranty_certificate",
            expiry_date=future,
        )
        self.user.settings.enable_email_notifications = False
        self.user.settings.enable_push_notifications = False
        self.user.settings.save()
        from records.tasks import send_expiry_notifications

        result = send_expiry_notifications()
        self.assertIsNone(result)


def _make_plaid_record(user, title: str, **overrides) -> Record:
    defaults = dict(
        merchant=title,
        balance=Decimal("100.00"),
        transaction_date=date(2024, 6, 15),
        record_type=Record.RecordTypes.EXPENSE_RECEIPT,
        plaid_transaction_id=title.lower().replace(" ", "_"),
    )
    defaults.update(overrides)
    return Record.objects.create(user=user, title=title, **defaults)


def _make_doc_record(user, title: str, **overrides) -> Record:
    defaults = dict(
        merchant=title,
        balance=Decimal("100.00"),
        transaction_date=date(2024, 6, 15),
        record_type=Record.RecordTypes.FINANCIAL_DOCUMENT,
        products="Test Product",
        notes=f"Note for {title}",
    )
    defaults.update(overrides)
    return Record.objects.create(user=user, title=title, **defaults)


class CalculateMatchScoreTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="match_score", password="pass")
        self.plaid = _make_plaid_record(self.user, "Amazon Purchase")
        self.doc = _make_doc_record(self.user, "Amazon Purchase")

    def test_perfect_match(self):
        score = calculate_match_score(self.plaid, self.doc)
        self.assertEqual(score, 120)

    def test_no_match(self):
        doc = _make_doc_record(
            self.user,
            "Something Completely Different",
            balance=Decimal("999.99"),
            transaction_date=date(2024, 1, 1),
        )
        score = calculate_match_score(self.plaid, doc)
        self.assertLess(score, MERGE_SCORE_THRESHOLD)

    def test_balance_only_match(self):
        doc = _make_doc_record(self.user, "Unrelated", merchant="", transaction_date=None)
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 40)

    def test_date_only_match(self):
        doc = _make_doc_record(self.user, "Unrelated", balance=None, merchant="")
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 30)

    def test_merchant_partial_match(self):
        doc = _make_doc_record(
            self.user, "Something", merchant="amazon", balance=None, transaction_date=None
        )
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 10)

    def test_title_partial_match(self):
        doc = _make_doc_record(
            self.user, "amazon", merchant="", balance=None, transaction_date=None
        )
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 8)

    def test_balance_within_tolerance(self):
        doc = _make_doc_record(self.user, "Amazon Purchase", balance=Decimal("100.50"))
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 110)

    def test_date_one_day_off(self):
        doc = _make_doc_record(self.user, "Amazon Purchase", transaction_date=date(2024, 6, 16))
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 110)

    def test_both_none_balance_and_date(self):
        doc = _make_doc_record(self.user, "Amazon Purchase", balance=None, transaction_date=None)
        score = calculate_match_score(self.plaid, doc)
        self.assertGreaterEqual(score, 50)

    def test_empty_strings_do_not_raise(self):
        self.plaid.merchant = ""
        self.plaid.title = ""
        doc = _make_doc_record(
            self.user, "Something", merchant="", balance=None, transaction_date=None
        )
        score = calculate_match_score(self.plaid, doc)
        self.assertEqual(score, 0)


class FindBestPlaidMatchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="best_plaid", password="pass")
        self.plaid = _make_plaid_record(self.user, "Target Purchase")
        self.doc = _make_doc_record(self.user, "Target Purchase")

    def test_finds_match(self):
        match = find_best_plaid_match(self.doc)
        self.assertIsNotNone(match)
        self.assertEqual(match.pk, self.plaid.pk)

    def test_no_match_below_threshold(self):
        doc = _make_doc_record(
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
        plaid = _make_plaid_record(other, "Self")
        match = find_best_plaid_match(plaid)
        self.assertIsNone(match)

    def test_user_isolation(self):
        other = User.objects.create_user(username="other_best", password="pass")
        _make_plaid_record(other, "Other User Purchase")
        doc = _make_doc_record(
            other,
            "Other User Purchase",
            balance=Decimal("100.00"),
            transaction_date=date(2024, 6, 15),
        )
        match = find_best_plaid_match(doc)
        self.assertEqual(match.title, "Other User Purchase")
        self.plaid.delete()
        my_doc = _make_doc_record(
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
        _make_plaid_record(
            self.user, "Worse Match", balance=Decimal("50.00"), transaction_date=date(2024, 1, 1)
        )
        match = find_best_plaid_match(self.doc)
        self.assertEqual(match.pk, self.plaid.pk)


class FindDocumentMatchesForPlaidTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="find_doc", password="pass")
        self.plaid = _make_plaid_record(self.user, "Best Buy")

    def test_finds_matching_docs(self):
        doc = _make_doc_record(self.user, "Best Buy")
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0].pk, doc.pk)

    def test_returns_empty_when_no_match(self):
        _make_doc_record(self.user, "Not Matching", balance=Decimal("999.99"), merchant="Different")
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(matches, [])

    def test_multiple_matches_sorted_by_score(self):
        perfect = _make_doc_record(self.user, "Best Buy")
        partial = _make_doc_record(
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
        _make_doc_record(other, "Best Buy")
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(matches, [])

    def test_ignores_inactive_docs(self):
        _make_doc_record(self.user, "Best Buy", is_active=False)
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(matches, [])

    def test_ignores_docs_with_plaid_id(self):
        _make_doc_record(self.user, "Best Buy", plaid_transaction_id="already_merged")
        matches = find_document_matches_for_plaid(self.plaid)
        self.assertEqual(matches, [])


class MergeDocumentIntoPlaidTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="merge_doc", password="pass")
        self.plaid = _make_plaid_record(self.user, "Walmart")
        self.doc = _make_doc_record(
            self.user, "Walmart", products="Milk|Eggs", notes="Weekly groceries"
        )
        self.doc_folder = Folder.objects.create(user=self.user, name="Groceries")
        self.plaid_with_folder = _make_plaid_record(self.user, "Costco", folder=self.doc_folder)
        self.doc_with_docref = _make_doc_record(
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
        doc_with_folder = _make_doc_record(
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

    def test_merge_with_document_reference(self):
        import hashlib
        from documents.models import DocumentData

        doc_data = DocumentData.objects.create(
            user=self.user,
            associated_record=self.doc_with_docref,
            filepath="users/1/test.pdf",
            file_hash=hashlib.sha256(b"test").hexdigest(),
        )
        merge_document_into_plaid(self.plaid, self.doc_with_docref, doc_data)
        doc_data.refresh_from_db()
        self.assertEqual(doc_data.associated_record, self.plaid)

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
        plaid = _make_plaid_record(
            self.user,
            "Bank Fee",
            record_type=Record.RecordTypes.EXPENSE_RECEIPT,
        )
        doc = _make_doc_record(
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
        self.plaid = _make_plaid_record(self.user, "Home Depot")
        self.doc = _make_doc_record(
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


class TryMatchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="try_match", password="pass")

    def test_try_match_document_record_found(self):
        plaid = _make_plaid_record(self.user, "Staples")
        doc = _make_doc_record(self.user, "Staples")
        result = try_match_document_record(doc)
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, plaid.pk)

    def test_try_match_document_record_not_found(self):
        doc = _make_doc_record(
            self.user,
            "Unique Item No Match",
            balance=Decimal("999.99"),
            transaction_date=date(2020, 1, 1),
            merchant="Nowhere",
        )
        result = try_match_document_record(doc)
        self.assertIsNone(result)

    def test_try_match_plaid_record_found(self):
        plaid = _make_plaid_record(self.user, "Office Depot")
        _make_doc_record(self.user, "Office Depot")
        merged = try_match_plaid_record(plaid)
        self.assertEqual(len(merged), 1)

    def test_try_match_plaid_record_multiple(self):
        plaid = _make_plaid_record(self.user, "Multi Store")
        _make_doc_record(self.user, "Multi Store")
        _make_doc_record(
            self.user, "Multi Store", balance=Decimal("100.00"), transaction_date=date(2024, 6, 15)
        )
        merged = try_match_plaid_record(plaid)
        self.assertEqual(len(merged), 2)

    def test_try_match_plaid_record_not_found(self):
        plaid = _make_plaid_record(self.user, "Solo")
        merged = try_match_plaid_record(plaid)
        self.assertEqual(merged, [])

    def test_try_match_plaid_record_no_double_merge(self):
        plaid = _make_plaid_record(self.user, "Single")
        doc = _make_doc_record(self.user, "Single")
        try_match_plaid_record(plaid)
        doc.refresh_from_db()
        self.assertFalse(doc.is_active)
        merged_again = try_match_plaid_record(plaid)
        self.assertEqual(merged_again, [])


class MergeLogModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="merge_log_model", password="pass")
        self.plaid = _make_plaid_record(self.user, "Model Test")
        self.doc = _make_doc_record(self.user, "Model Test")
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
        log2 = MergeLog.objects.create(
            plaid_record=self.plaid,
            document_record=self.doc,
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
        plaid = _make_plaid_record(self.user, "Signal Plaid")
        plaid.title = "Updated Plaid"
        self._assert_on_commit_called(lambda: plaid.save())


class MergeListViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="merge_list_view", password="pass")
        self.url = reverse("records:merge_list")
        self.plaid = _make_plaid_record(self.user, "View Test")
        self.doc = _make_doc_record(self.user, "View Test")
        self.log = MergeLog.objects.create(
            plaid_record=self.plaid,
            document_record=self.doc,
            plaid_snapshot={},
            document_snapshot={},
        )

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_authenticated_access(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "records/merge_list.html")

    def test_shows_merges(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertIn("merges", response.context)
        self.assertEqual(len(response.context["merges"]), 1)

    def test_user_isolation(self):
        other = User.objects.create_user(username="other_ml", password="pass")
        plaid2 = _make_plaid_record(other, "Other Merge")
        doc2 = _make_doc_record(other, "Other Merge")
        MergeLog.objects.create(
            plaid_record=plaid2,
            document_record=doc2,
            plaid_snapshot={},
            document_snapshot={},
        )
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["merges"]), 1)

    def test_htmx_partial(self):
        self.client.force_login(self.user)
        response = self.client.get(
            self.url,
            HTTP_HX_TARGET="merge-list-container",
        )
        self.assertTemplateUsed(response, "records/partials/merge_list_partial.html")


class UndoMergeViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="undo_view", password="pass")
        self.plaid = _make_plaid_record(self.user, "Undo View")
        self.doc = _make_doc_record(self.user, "Undo View")
        self.merge_log = MergeLog.objects.create(
            plaid_record=self.plaid,
            document_record=self.doc,
            plaid_snapshot=_record_snapshot(self.plaid),
            document_snapshot=_record_snapshot(self.doc),
        )
        self.url = reverse("records:undo_merge", args=[self.merge_log.pk])

    def test_login_required(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)

    def test_owner_can_undo(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url)
        self.assertIn(response.status_code, [200, 302, 204])
        self.doc.refresh_from_db()
        self.assertTrue(self.doc.is_active)

    def test_other_user_cannot_undo(self):
        other = User.objects.create_user(username="other_undo", password="pass")
        self.client.force_login(other)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)
        self.doc.refresh_from_db()
        self.assertTrue(self.doc.is_active)

    def test_idempotent(self):
        self.client.force_login(self.user)
        response1 = self.client.post(self.url)
        response2 = self.client.post(self.url)
        self.assertIn(response1.status_code, [200, 302, 204])
        self.assertIn(response2.status_code, [200, 302, 204])
        self.merge_log.refresh_from_db()
        self.assertIsNotNone(self.merge_log.undone_at)

    def test_htmx_undo_returns_204(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        response["HX-Trigger"]
        self.doc.refresh_from_db()
        self.assertTrue(self.doc.is_active)

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)
