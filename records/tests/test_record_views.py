from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from records.models import Record, AuditLog


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
        Record.objects.create(
            user=user2,
            title="Other's",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
        )
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["records"]), 0)

    def test_records_visible(self):
        self.client.force_login(self.user)
        Record.objects.create(
            user=self.user,
            title="My Record",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
        )
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["records"]), 1)

    def test_filter_by_search_query(self):
        self.client.force_login(self.user)
        Record.objects.create(
            user=self.user,
            title="UniqueWidget",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
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
                transaction_date=date(2024, 6, 15),
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
                transaction_date=date(2024, 6, 15),
            )
        response = self.client.get(self.url, {"page": 2})
        self.assertEqual(len(response.context["records"]), 5)

    def test_pagination_invalid_page_returns_404(self):
        self.client.force_login(self.user)
        Record.objects.create(
            user=self.user,
            title="Test",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
        )
        response = self.client.get(self.url, {"page": "abc"})
        self.assertEqual(response.status_code, 404)

    def test_pagination_out_of_range_page_returns_404(self):
        self.client.force_login(self.user)
        for i in range(30):
            Record.objects.create(
                user=self.user,
                title=f"Record {i}",
                record_type="expense_receipt",
                transaction_date=date(2024, 6, 15),
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
                "transaction_date": "2024-06-15",
                "merchant": "Test Merchant",
                "balance": "25.00",
                "notes": "Business purpose",
                "payment_method": "Credit Card",
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
                "merchant": "Test Merchant",
                "balance": "100.00",
            },
        )
        self.assertIn(response.status_code, [200, 302])
        record = Record.objects.get(title="With Expiry")
        self.assertEqual(record.expiry_date, date(2024, 12, 31))

    def test_post_with_folder(self):
        from records.models import Folder

        self.client.force_login(self.user)
        folder = Folder.objects.create(user=self.user, name="Test Folder")
        response = self.client.post(
            self.url,
            {
                "title": "In Folder",
                "products": "Item",
                "record_type": "expense_receipt",
                "folder": folder.id,
                "transaction_date": "2024-06-15",
                "merchant": "Test Merchant",
                "balance": "50.00",
                "notes": "Business purpose",
                "payment_method": "Credit Card",
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
                "transaction_date": "2024-06-15",
                "merchant": "Test Merchant",
                "notes": "Business purpose",
                "payment_method": "Credit Card",
            },
        )
        self.assertIn(response.status_code, [200, 302])
        record = Record.objects.get(title="With Balance")
        self.assertEqual(record.balance, Decimal("250.00"))


class RecordDetailViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="detailuser", password="pass")
        self.record = Record.objects.create(
            user=self.user,
            title="Detail View",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
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
                "transaction_date": "2024-06-15",
                "merchant": "Test Merchant",
                "balance": "100.00",
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
            user=self.user,
            title="To Archive",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
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
            transaction_date=date(2024, 6, 15),
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
            user=self.user,
            title="To Delete",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
        )
        self.url = reverse("records:delete_record", args=[self.record.id])

    def test_owner_can_delete(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url)
        self.assertIn(response.status_code, [200, 302])
        self.record.refresh_from_db()
        self.assertFalse(self.record.is_active)

    def test_other_user_cannot_delete(self):
        user2 = User.objects.create_user(username="otherdel", password="pass")
        self.client.force_login(user2)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Record.objects.filter(id=self.record.id).exists())


class HardDeleteViewHTTPTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="harddel_http", password="pass")
        self.url_name = "records:hard_delete_record"

    def _make_old_record(self):
        old_date = timezone.now().date() - timedelta(days=365 * 8)
        record = Record.objects.create(
            user=self.user,
            title="Old Record",
            record_type="expense_receipt",
            transaction_date=date(2015, 6, 15),
        )
        Record.objects.filter(pk=record.pk).update(date_added=old_date)
        record.refresh_from_db()
        return record

    def _make_young_record(self):
        return Record.objects.create(
            user=self.user,
            title="Young Record",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
        )

    def test_too_young_htmx_returns_409(self):
        record = self._make_young_record()
        self.client.force_login(self.user)
        url = reverse(self.url_name, args=[record.pk])
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 409)
        import json

        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["showToast"]["tags"], "error")

    def test_too_young_non_htmx_redirects(self):
        record = self._make_young_record()
        self.client.force_login(self.user)
        url = reverse(self.url_name, args=[record.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("record_detail", response.url)

    def test_old_record_htmx_returns_204(self):
        record = self._make_old_record()
        self.client.force_login(self.user)
        url = reverse(self.url_name, args=[record.pk])
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Record.objects.filter(pk=record.pk).exists())

    def test_old_record_creates_audit_log(self):
        record = self._make_old_record()
        self.client.force_login(self.user)
        url = reverse(self.url_name, args=[record.pk])
        self.client.post(url)
        audit = AuditLog.objects.filter(
            user=self.user,
            action=AuditLog.Action.HARD_DELETE,
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.details.get("title"), "Old Record")

    def test_other_user_cannot_delete(self):
        record = self._make_old_record()
        other = User.objects.create_user(username="other_hd", password="pass")
        self.client.force_login(other)
        url = reverse(self.url_name, args=[record.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Record.objects.filter(pk=record.pk).exists())


class ArchiveViewHTTPTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="archive_http", password="pass")

    def test_archive_deactivates_record(self):
        record = Record.objects.create(
            user=self.user,
            title="To Archive",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
        )
        self.client.force_login(self.user)
        url = reverse("records:archive_record", args=[record.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertFalse(record.is_active)

    def test_archive_creates_audit_log(self):
        record = Record.objects.create(
            user=self.user,
            title="Audit Archive",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
        )
        self.client.force_login(self.user)
        url = reverse("records:archive_record", args=[record.pk])
        self.client.post(url)
        audit = AuditLog.objects.filter(
            user=self.user,
            action=AuditLog.Action.ARCHIVE,
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.record_id, record.pk)

    def test_unarchive_reactivates_record(self):
        record = Record.objects.create(
            user=self.user,
            title="To Unarchive",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
            is_active=False,
        )
        self.client.force_login(self.user)
        url = reverse("records:unarchive_record", args=[record.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertTrue(record.is_active)

    def test_unarchive_creates_audit_log(self):
        record = Record.objects.create(
            user=self.user,
            title="Audit Unarchive",
            record_type="expense_receipt",
            transaction_date=date(2024, 6, 15),
            is_active=False,
        )
        self.client.force_login(self.user)
        url = reverse("records:unarchive_record", args=[record.pk])
        self.client.post(url)
        audit = AuditLog.objects.filter(
            user=self.user,
            action=AuditLog.Action.UNARCHIVE,
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.record_id, record.pk)
