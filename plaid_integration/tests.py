import hashlib
import json
from datetime import date, timedelta

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from plaid_integration.models import PlaidItem
from plaid_integration.services import public_token_exchange
from plaid_integration.tasks import (
    choose_folder,
    sync_and_convert_for_item_task,
    _get_payment_method,
    _txn_to_record_defaults,
)
from plaid_integration.views import (
    verify_plaid_webhook,
    plaid_webhook,
    CreateLinkTokenView,
    PublicTokenExchange,
    PlaidStatusView,
    DisconnectBankView,
)
from records.models import Record, Folder


class PlaidItemModelTest(TestCase):
    """Tests for the PlaidItem model."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.plaid_item = PlaidItem.objects.create(
            user=self.user,
            item_id="test-item-123",
            access_token="access-test-456",
            institution_name="Test Bank",
            accounts_data=[
                {
                    "id": "acc1",
                    "name": "Checking",
                    "mask": "1234",
                    "type": "depository",
                    "subtype": "checking",
                },
            ],
        )

    def test_str_representation(self):
        self.assertEqual(str(self.plaid_item), "Test Bank (test-item-123)")

    def test_str_no_institution_name(self):
        item = PlaidItem.objects.create(
            user=self.user,
            item_id="item-no-name",
            access_token="access-no-name",
        )
        self.assertEqual(str(item), "item-no-name (item-no-name)")

    def test_user_relationship(self):
        self.assertEqual(self.plaid_item.user, self.user)

    def test_item_id_unique(self):
        with self.assertRaises(Exception):
            PlaidItem.objects.create(
                user=self.user,
                item_id="test-item-123",
                access_token="access-duplicate",
            )

    def test_auto_timestamps(self):
        self.assertIsNotNone(self.plaid_item.created_at)
        self.assertIsNotNone(self.plaid_item.updated_at)

    def test_nullable_fields(self):
        item = PlaidItem.objects.create(
            user=self.user,
            item_id="item-minimal",
            access_token="access-minimal",
        )
        self.assertIsNone(item.next_cursor)
        self.assertIsNone(item.last_error_code)
        self.assertIsNone(item.last_error_message)
        self.assertIsNone(item.last_error_at)
        self.assertIsNone(item.institution_name)
        self.assertIsNone(item.accounts_data)


class ChooseFolderTest(TestCase):
    """Tests for the choose_folder utility function."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")

    def test_returns_none_for_empty_category(self):
        self.assertIsNone(choose_folder(self.user, None))
        self.assertIsNone(choose_folder(self.user, ""))
        self.assertIsNone(choose_folder(self.user, "   "))

    def test_creates_folder_when_not_exists(self):
        folder = choose_folder(self.user, "Groceries")
        self.assertIsNotNone(folder)
        self.assertEqual(folder.name, "Groceries")
        self.assertEqual(folder.user, self.user)

    def test_returns_existing_folder(self):
        existing = Folder.objects.create(user=self.user, name="Groceries")
        folder = choose_folder(self.user, "Groceries")
        self.assertEqual(folder.id, existing.id)

    def test_fuzzy_match_existing_folder(self):
        Folder.objects.create(user=self.user, name="Groceries")
        folder = choose_folder(self.user, "Food and Groceries")
        self.assertIsNotNone(folder)

    def test_folder_cache_hit(self):
        cache = {}
        folder1 = choose_folder(self.user, "Groceries", folder_cache=cache)
        folder2 = choose_folder(self.user, "Groceries", folder_cache=cache)
        self.assertEqual(folder1.id, folder2.id)
        self.assertIn("Groceries", cache)

    def test_returns_none_for_whitespace_only_words(self):
        self.assertIsNone(choose_folder(self.user, "   "))


class GetPaymentMethodTest(TestCase):
    """Tests for the _get_payment_method helper."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.plaid_item = PlaidItem.objects.create(
            user=self.user,
            item_id="item-1",
            access_token="access-1",
            accounts_data=[
                {"id": "acc1", "name": "Checking", "mask": "1234"},
                {"id": "acc2", "name": "Savings", "mask": "5678"},
            ],
        )

    def test_returns_formatted_payment_method(self):
        result = _get_payment_method(self.plaid_item, "acc1")
        self.assertEqual(result, "Checking (\u2022\u20221234)")

    def test_returns_name_only_when_no_mask(self):
        item = PlaidItem.objects.create(
            user=self.user,
            item_id="item-no-mask",
            access_token="access-no-mask",
            accounts_data=[{"id": "acc1", "name": "Checking"}],
        )
        result = _get_payment_method(item, "acc1")
        self.assertEqual(result, "Checking")

    def test_returns_empty_for_no_accounts(self):
        item = PlaidItem.objects.create(
            user=self.user,
            item_id="item-empty",
            access_token="access-empty",
            accounts_data=None,
        )
        result = _get_payment_method(item, "acc1")
        self.assertEqual(result, "")

    def test_returns_empty_for_unknown_account(self):
        result = _get_payment_method(self.plaid_item, "unknown")
        self.assertEqual(result, "")

    def test_returns_empty_for_empty_account_id(self):
        result = _get_payment_method(self.plaid_item, "")
        self.assertEqual(result, "")


class TxnToRecordDefaultsTest(TestCase):
    """Tests for the _txn_to_record_defaults helper."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.user.settings.auto_create_and_organize_folders = False
        self.user.settings.save()
        self.plaid_item = PlaidItem.objects.create(
            user=self.user,
            item_id="item-1",
            access_token="access-1",
            accounts_data=[{"id": "acc1", "name": "Checking", "mask": "1234"}],
        )

    def test_basic_transaction_conversion(self):
        txn = {
            "name": "Amazon Purchase",
            "merchant_name": "Amazon",
            "amount": 49.99,
            "date": "2024-06-15",
            "authorized_date": "2024-06-14",
            "account_id": "acc1",
            "category": ["Shopping", "Online"],
        }
        defaults = _txn_to_record_defaults(txn, self.plaid_item)
        self.assertEqual(defaults["title"], "Amazon Purchase")
        self.assertEqual(defaults["merchant"], "Amazon")
        self.assertEqual(defaults["balance"], 49.99)
        self.assertEqual(defaults["transaction_date"], date(2024, 6, 14))
        self.assertEqual(defaults["user"], self.user)

    def test_falls_back_to_name_when_no_merchant(self):
        txn = {
            "name": "Walmart",
            "amount": 25.00,
            "date": "2024-06-15",
            "account_id": "acc1",
            "category": [],
        }
        defaults = _txn_to_record_defaults(txn, self.plaid_item)
        self.assertEqual(defaults["merchant"], "Walmart")

    def test_falls_back_to_date_when_no_authorized_date(self):
        txn = {
            "name": "Store",
            "amount": 10.00,
            "date": "2024-06-15",
            "account_id": "acc1",
        }
        defaults = _txn_to_record_defaults(txn, self.plaid_item)
        self.assertEqual(defaults["transaction_date"], date(2024, 6, 15))

    def test_auto_folder_creation_enabled(self):
        self.user.settings.auto_create_and_organize_folders = True
        self.user.settings.save()
        txn = {
            "name": "Kroger",
            "amount": 50.00,
            "date": "2024-06-15",
            "account_id": "acc1",
            "category": ["Groceries"],
        }
        defaults = _txn_to_record_defaults(txn, self.plaid_item)
        self.assertIsNotNone(defaults["folder"])

    def test_payment_method_populated(self):
        txn = {
            "name": "Store",
            "amount": 10.00,
            "date": "2024-06-15",
            "account_id": "acc1",
        }
        defaults = _txn_to_record_defaults(txn, self.plaid_item)
        self.assertIn("Checking", defaults["payment_method"])


class PublicTokenExchangeTest(TestCase):
    """Tests for the public_token_exchange service."""

    @patch("plaid_integration.services.plaid_client")
    def test_successful_exchange(self, mock_client):
        mock_response = {
            "access_token": "access-xxx",
            "item_id": "item-yyy",
        }
        mock_client.item_public_token_exchange.return_value = mock_response

        access_token, item_id = public_token_exchange("public-token-123")
        self.assertEqual(access_token, "access-xxx")
        self.assertEqual(item_id, "item-yyy")

    @patch("plaid_integration.services.plaid_client")
    def test_api_error_raises(self, mock_client):
        import plaid

        mock_client.item_public_token_exchange.side_effect = plaid.ApiException(
            status=400, reason="Bad Request"
        )
        with self.assertRaises(plaid.ApiException):
            public_token_exchange("bad-token")

    @patch("plaid_integration.services.plaid_client")
    def test_unexpected_error_raises(self, mock_client):
        mock_client.item_public_token_exchange.side_effect = RuntimeError("Unexpected")
        with self.assertRaises(RuntimeError):
            public_token_exchange("token")


class WebhookVerificationTest(TestCase):
    """Tests for Plaid webhook signature verification."""

    def test_missing_verification_header(self):
        self.assertFalse(verify_plaid_webhook(b"body", None))
        self.assertFalse(verify_plaid_webhook(b"body", ""))

    @patch("plaid_integration.views.webhook._get_plaid_jwk")
    def test_invalid_jwt_returns_false(self, mock_jwk):
        result = verify_plaid_webhook(b"body", "not-a-valid-jwt")
        self.assertFalse(result)
        mock_jwk.assert_not_called()

    @patch("plaid_integration.views.webhook._get_plaid_jwk")
    def test_no_jwk_found_returns_false(self, mock_jwk):
        mock_jwk.return_value = None
        import jwt as pyjwt
        from datetime import UTC, datetime

        token = pyjwt.encode(
            {"kid": "unknown-kid", "exp": datetime.max.replace(tzinfo=UTC).timestamp()},
            "secret",
            algorithm="HS256",
        )
        result = verify_plaid_webhook(b"body", token)
        self.assertFalse(result)

    def test_body_hash_mismatch_returns_false(self):
        import jwt as pyjwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from datetime import UTC, datetime
        import base64

        def _int_to_base64url(n):
            byte_length = (n.bit_length() + 7) // 8
            return (
                base64.urlsafe_b64encode(n.to_bytes(byte_length, byteorder="big"))
                .rstrip(b"=")
                .decode()
            )

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()

        body_hash = hashlib.sha256(b"original body").hexdigest()
        token = pyjwt.encode(
            {
                "kid": "test-kid",
                "request_body_sha256": body_hash,
                "exp": datetime.max.replace(tzinfo=UTC).timestamp(),
            },
            private_key,
            algorithm="RS256",
        )

        with patch("plaid_integration.views.webhook._get_plaid_jwk") as mock_jwk:
            pub_numbers = public_key.public_numbers()
            public_jwk = {
                "kty": "RSA",
                "n": _int_to_base64url(pub_numbers.n),
                "e": _int_to_base64url(pub_numbers.e),
                "kid": "test-kid",
            }
            mock_jwk.return_value = public_jwk
            result = verify_plaid_webhook(b"different body", token)
            self.assertFalse(result)


class SyncAndConvertTaskTest(TestCase):
    """Tests for the sync_and_convert_for_item_task background task."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.plaid_item = PlaidItem.objects.create(
            user=self.user,
            item_id="item-1",
            access_token="access-1",
        )

    @patch("records.matching.try_match_plaid_record")
    @patch("plaid_integration.tasks.client")
    def test_sync_creates_records(self, mock_client, mock_match):
        mock_response = {
            "added": [
                {
                    "transaction_id": "txn-001",
                    "name": "Coffee Shop",
                    "amount": 5.50,
                    "date": "2024-06-15",
                    "account_id": "acc1",
                    "category": ["Food and Drink"],
                }
            ],
            "modified": [],
            "removed": [],
            "next_cursor": "cursor-abc",
            "has_more": False,
        }
        mock_client.transactions_sync.return_value = mock_response

        result = sync_and_convert_for_item_task(self.plaid_item.id)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["modified"], 0)
        self.assertEqual(result["removed"], 0)
        self.assertTrue(Record.objects.filter(plaid_transaction_id="txn-001").exists())

    @patch("records.matching.try_match_plaid_record")
    @patch("plaid_integration.tasks.client")
    def test_sync_removes_deactivated_records(self, mock_client, mock_match):
        record = Record.objects.create(
            user=self.user,
            title="Old Transaction",
            transaction_date=date(2024, 6, 1),
            plaid_transaction_id="txn-old",
            plaid_item=self.plaid_item,
        )
        mock_response = {
            "added": [],
            "modified": [],
            "removed": [{"transaction_id": "txn-old"}],
            "next_cursor": "cursor-xyz",
            "has_more": False,
        }
        mock_client.transactions_sync.return_value = mock_response

        result = sync_and_convert_for_item_task(self.plaid_item.id)
        self.assertEqual(result["removed"], 1)
        record.refresh_from_db()
        self.assertFalse(record.is_active)

    @patch("plaid_integration.tasks.client")
    def test_sync_handles_api_error_with_retry(self, mock_client):
        mock_client.transactions_sync.side_effect = Exception("API Error")

        with self.assertRaises(Exception):
            sync_and_convert_for_item_task(self.plaid_item.id)

    @patch("records.matching.try_match_plaid_record")
    @patch("plaid_integration.tasks.client")
    def test_sync_updates_cursor(self, mock_client, mock_match):
        mock_response = {
            "added": [],
            "modified": [],
            "removed": [],
            "next_cursor": "new-cursor-123",
            "has_more": False,
        }
        mock_client.transactions_sync.return_value = mock_response

        sync_and_convert_for_item_task(self.plaid_item.id)
        self.plaid_item.refresh_from_db()
        self.assertEqual(self.plaid_item.next_cursor, "new-cursor-123")

    @patch("records.matching.try_match_plaid_record")
    @patch("plaid_integration.tasks.client")
    def test_sync_handles_pagination(self, mock_client, mock_match):
        page1 = {
            "added": [
                {
                    "transaction_id": "txn-1",
                    "name": "T1",
                    "amount": 10,
                    "date": "2024-06-15",
                    "account_id": "a1",
                }
            ],
            "modified": [],
            "removed": [],
            "next_cursor": "cursor-2",
            "has_more": True,
        }
        page2 = {
            "added": [
                {
                    "transaction_id": "txn-2",
                    "name": "T2",
                    "amount": 20,
                    "date": "2024-06-16",
                    "account_id": "a1",
                }
            ],
            "modified": [],
            "removed": [],
            "next_cursor": "cursor-done",
            "has_more": False,
        }
        mock_client.transactions_sync.side_effect = [page1, page2]

        result = sync_and_convert_for_item_task(self.plaid_item.id)
        self.assertEqual(result["added"], 2)

    @patch("records.matching.try_match_plaid_record")
    @patch("plaid_integration.tasks.client")
    def test_nonexistent_plaid_item_returns_error(self, mock_client, mock_match):
        result = sync_and_convert_for_item_task(99999)
        self.assertIn("error", result)


class PlaidViewsTest(TestCase):
    """Tests for Plaid integration API views."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.client.login(username="testuser", password="pass")
        self.plaid_item = PlaidItem.objects.create(
            user=self.user,
            item_id="item-123",
            access_token="access-123",
            institution_name="Test Bank",
        )

    @patch("plaid_integration.views.link.client")
    def test_create_link_token(self, mock_client):
        mock_client.link_token_create.return_value = {"link_token": "link-xxx"}
        response = self.client.post(reverse("plaid:create_link_token"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["link_token"], "link-xxx")

    @patch("plaid_integration.views.link.client")
    def test_create_link_token_api_error(self, mock_client):
        import plaid

        mock_client.link_token_create.side_effect = plaid.ApiException(
            status=400, reason="Bad Request"
        )
        response = self.client.post(reverse("plaid:create_link_token"))
        self.assertEqual(response.status_code, 400)

    @patch("plaid_integration.views.link.client")
    def test_create_update_link_token(self, mock_client):
        mock_client.link_token_create.return_value = {"link_token": "link-update"}
        response = self.client.post(reverse("plaid:create_update_link_token", args=["item-123"]))
        self.assertEqual(response.status_code, 200)

    def test_create_update_link_token_not_found(self):
        response = self.client.post(reverse("plaid:create_update_link_token", args=["nonexistent"]))
        self.assertEqual(response.status_code, 404)

    def test_plaid_status_connected(self):
        response = self.client.get(reverse("plaid:status"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["connected"])
        self.assertEqual(len(data["items"]), 1)

    def test_plaid_status_not_connected(self):
        cache.clear()
        self.plaid_item.delete()
        response = self.client.get(reverse("plaid:status"))
        data = response.json()
        self.assertFalse(data["connected"])

    def test_plaid_status_cached(self):
        response1 = self.client.get(reverse("plaid:status"))
        response2 = self.client.get(reverse("plaid:status"))
        self.assertEqual(response1.json(), response2.json())

    def test_disconnect_bank(self):
        response = self.client.post(reverse("plaid:disconnect", args=["item-123"]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PlaidItem.objects.filter(item_id="item-123").exists())

    def test_disconnect_bank_not_found(self):
        response = self.client.post(reverse("plaid:disconnect", args=["nonexistent"]))
        self.assertEqual(response.status_code, 404)

    @patch("plaid_integration.views.status.client")
    def test_sync_transactions(self, mock_client):
        response = self.client.post(
            reverse("plaid:sync"),
            data=json.dumps({"item_id": "item-123"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    @patch("plaid_integration.views.status.client")
    def test_sync_transactions_no_item(self, mock_client):
        self.plaid_item.delete()
        response = self.client.post(reverse("plaid:sync"))
        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_access_redirects(self):
        self.client.logout()
        response = self.client.get(reverse("plaid:status"))
        self.assertEqual(response.status_code, 403)


class PlaidWebhookViewTest(TestCase):
    """Tests for the Plaid webhook receiver endpoint."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.plaid_item = PlaidItem.objects.create(
            user=self.user,
            item_id="item-webhook-1",
            access_token="access-wh-1",
        )

    @override_settings(PLAID_ENV="sandbox")
    @patch("plaid_integration.views.webhook.sync_and_convert_for_item_task")
    def test_sync_updates_available_webhook(self, mock_task):
        payload = {
            "webhook_type": "TRANSACTIONS",
            "webhook_code": "SYNC_UPDATES_AVAILABLE",
            "item_id": "item-webhook-1",
        }
        request = self.factory.post(
            reverse("plaid:webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        response = plaid_webhook(request)
        self.assertEqual(response.status_code, 200)
        mock_task.delay.assert_called_once_with(self.plaid_item.id)

    @override_settings(PLAID_ENV="sandbox")
    def test_item_login_required_webhook(self):
        payload = {
            "webhook_type": "ITEM",
            "webhook_code": "ITEM_LOGIN_REQUIRED",
            "item_id": "item-webhook-1",
        }
        request = self.factory.post(
            reverse("plaid:webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        response = plaid_webhook(request)
        self.assertEqual(response.status_code, 200)
        self.plaid_item.refresh_from_db()
        self.assertEqual(self.plaid_item.last_error_code, "ITEM_LOGIN_REQUIRED")

    @override_settings(PLAID_ENV="sandbox")
    def test_transactions_removed_webhook(self):
        Record.objects.create(
            user=self.user,
            title="To Remove",
            transaction_date=date(2024, 6, 1),
            plaid_transaction_id="txn-remove-me",
            plaid_item=self.plaid_item,
        )
        payload = {
            "webhook_type": "TRANSACTIONS",
            "webhook_code": "TRANSACTIONS_REMOVED",
            "item_id": "item-webhook-1",
            "removed_transactions": ["txn-remove-me"],
        }
        request = self.factory.post(
            reverse("plaid:webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        response = plaid_webhook(request)
        self.assertEqual(response.status_code, 200)
        record = Record.objects.get(plaid_transaction_id="txn-remove-me")
        self.assertFalse(record.is_active)

    @override_settings(PLAID_ENV="sandbox")
    def test_error_webhook(self):
        payload = {
            "webhook_type": "ITEM",
            "webhook_code": "ERROR",
            "item_id": "item-webhook-1",
            "error": {
                "error_code": "ITEM_NOT_FOUND",
                "error_message": "Item not found",
            },
        }
        request = self.factory.post(
            reverse("plaid:webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        response = plaid_webhook(request)
        self.assertEqual(response.status_code, 200)
        self.plaid_item.refresh_from_db()
        self.assertEqual(self.plaid_item.last_error_code, "ITEM_NOT_FOUND")

    @override_settings(PLAID_ENV="sandbox")
    def test_webhook_unknown_item_returns_ok(self):
        payload = {
            "webhook_type": "TRANSACTIONS",
            "webhook_code": "SYNC_UPDATES_AVAILABLE",
            "item_id": "unknown-item",
        }
        request = self.factory.post(
            reverse("plaid:webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        response = plaid_webhook(request)
        self.assertEqual(response.status_code, 200)

    @override_settings(PLAID_ENV="sandbox")
    def test_webhook_invalid_json(self):
        request = self.factory.post(
            reverse("plaid:webhook"),
            data="not json",
            content_type="application/json",
        )
        response = plaid_webhook(request)
        self.assertEqual(response.status_code, 400)

    @override_settings(PLAID_ENV="sandbox")
    def test_webhook_too_large(self):
        payload = json.dumps({"item_id": "x" * 200000})
        request = self.factory.post(
            reverse("plaid:webhook"),
            data=payload,
            content_type="application/json",
        )
        response = plaid_webhook(request)
        self.assertEqual(response.status_code, 400)

    @override_settings(PLAID_ENV="production")
    @patch("plaid_integration.views.webhook.verify_plaid_webhook")
    def test_production_mode_verifies_webhook(self, mock_verify):
        mock_verify.return_value = False
        payload = {
            "webhook_type": "TRANSACTIONS",
            "webhook_code": "SYNC_UPDATES_AVAILABLE",
            "item_id": "item-webhook-1",
        }
        request = self.factory.post(
            reverse("plaid:webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        response = plaid_webhook(request)
        self.assertEqual(response.status_code, 403)

    @override_settings(PLAID_ENV="sandbox")
    def test_pending_expiration_webhook(self):
        payload = {
            "webhook_type": "ITEM",
            "webhook_code": "PENDING_EXPIRATION",
            "item_id": "item-webhook-1",
        }
        request = self.factory.post(
            reverse("plaid:webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        response = plaid_webhook(request)
        self.assertEqual(response.status_code, 200)
        self.plaid_item.refresh_from_db()
        self.assertEqual(self.plaid_item.last_error_code, "PENDING_EXPIRATION")


class PlaidUrlsTest(TestCase):
    """Tests for URL resolution."""

    def test_webhook_url_resolves(self):
        url = reverse("plaid:webhook")
        self.assertEqual(url, "/plaid/webhook/")

    def test_status_url_resolves(self):
        url = reverse("plaid:status")
        self.assertEqual(url, "/plaid/status/")

    def test_sync_url_resolves(self):
        url = reverse("plaid:sync")
        self.assertEqual(url, "/plaid/sync/")

    def test_connect_url_resolves(self):
        url = reverse("plaid:connect")
        self.assertEqual(url, "/plaid/connect/")

    def test_disconnect_url_resolves(self):
        url = reverse("plaid:disconnect", args=["item-123"])
        self.assertEqual(url, "/plaid/disconnect/item-123/")
