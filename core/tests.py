import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse

from core.forms import UpdateUserSettingsForm
from core.middleware import HtmxMessageMiddleware
from core.models import UserSettings, Notification
from core.context_processors import webpush_status


class UserSettingsSignalTest(TestCase):
    def test_settings_created_with_user(self):
        user = User.objects.create_user(username="testuser", password="pass")
        self.assertTrue(hasattr(user, "settings"))
        self.assertIsInstance(user.settings, UserSettings)

    def test_settings_defaults(self):
        user = User.objects.create_user(username="testuser", password="pass")
        self.assertTrue(user.settings.auto_archive_expired_records)
        self.assertTrue(user.settings.auto_delete_archived_records)
        self.assertTrue(user.settings.enable_push_notifications)
        self.assertTrue(user.settings.enable_email_notifications)
        self.assertEqual(
            user.settings.expiring_notifications_advance_time,
            UserSettings.AdvanceTimeChoices.THREE_DAYS,
        )

    def test_settings_str(self):
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="pass"
        )
        self.assertEqual(str(user.settings), f"Settings for {user.email}")


class AdvanceTimeChoicesTest(TestCase):
    def test_choices_values(self):
        self.assertEqual(UserSettings.AdvanceTimeChoices.ONE_DAY, "1")
        self.assertEqual(UserSettings.AdvanceTimeChoices.THREE_DAYS, "3")
        self.assertEqual(UserSettings.AdvanceTimeChoices.ONE_WEEK, "7")
        self.assertEqual(UserSettings.AdvanceTimeChoices.ONE_MONTH, "30")


class NotificationModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")

    def test_create_notification(self):
        notification = Notification.objects.create(
            recipient=self.user,
            subject="Test Subject",
            message="Test message body",
        )
        self.assertEqual(notification.recipient, self.user)
        self.assertEqual(notification.subject, "Test Subject")
        self.assertEqual(notification.message, "Test message body")
        self.assertFalse(notification.is_read)
        self.assertIsNotNone(notification.sent_at)

    def test_notification_defaults(self):
        notification = Notification.objects.create(
            recipient=self.user, subject="Test", message="Body"
        )
        self.assertFalse(notification.is_read)


class LandingPageTest(TestCase):
    def test_status(self):
        response = self.client.get(reverse("core:landing_page"))
        self.assertEqual(response.status_code, 200)

    def test_template(self):
        response = self.client.get(reverse("core:landing_page"))
        self.assertTemplateUsed(response, "core/landing_page.html")


class PrivacyPolicyTest(TestCase):
    def test_status(self):
        response = self.client.get(reverse("core:privacy_policy"))
        self.assertEqual(response.status_code, 200)

    def test_template(self):
        response = self.client.get(reverse("core:privacy_policy"))
        self.assertTemplateUsed(response, "core/privacy_policy.html")


class HealthCheckTest(TestCase):
    def test_health_check_returns_200(self):
        response = self.client.get(reverse("core:health_check"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["database"], "connected")


class DashboardViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")

    def test_login_required(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_authenticated_access(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/dashboard.html")

    def test_context_has_counts(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:dashboard"))
        self.assertIn("active_records_count", response.context)
        self.assertIn("records", response.context)
        self.assertIn("expiring_soon", response.context)
        self.assertIn("orphaned_document_count", response.context)


class ProfilePageViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")

    def test_login_required(self):
        response = self.client.get(reverse("core:profile_page"))
        self.assertEqual(response.status_code, 302)

    def test_authenticated_access(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:profile_page"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/profile_page.html")

    def test_context_has_settings(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:profile_page"))
        self.assertIn("user_settings", response.context)
        self.assertEqual(response.context["user_settings"].user, self.user)

    def test_post_updates_settings(self):
        self.client.force_login(self.user)
        self.user.settings.refresh_from_db()
        response = self.client.post(
            reverse("core:profile_page"),
            {
                "auto_archive_expired_records": False,
                "auto_delete_archived_records": False,
                "enable_push_notifications": False,
                "enable_email_notifications": False,
                "expiring_notifications_advance_time": "7",
            },
        )
        self.assertIn(response.status_code, [200, 302])
        self.user.settings.refresh_from_db()
        self.assertFalse(self.user.settings.auto_archive_expired_records)
        self.assertFalse(self.user.settings.enable_email_notifications)
        self.assertEqual(self.user.settings.expiring_notifications_advance_time, "7")


class UpdateUserSettingsFormTest(TestCase):
    def test_form_fields(self):
        form = UpdateUserSettingsForm()
        expected = [
            "auto_archive_expired_records",
            "auto_delete_archived_records",
            "expiring_notifications_advance_time",
            "enable_push_notifications",
            "enable_email_notifications",
        ]
        self.assertEqual(list(form.fields.keys()), expected)

    def test_form_valid_data(self):
        form = UpdateUserSettingsForm(
            data={
                "auto_archive_expired_records": False,
                "auto_delete_archived_records": False,
                "enable_push_notifications": False,
                "enable_email_notifications": False,
                "expiring_notifications_advance_time": "30",
            }
        )
        self.assertTrue(form.is_valid())


class WebpushContextProcessorTest(TestCase):
    def test_unauthenticated(self):
        class MockUser:
            is_authenticated = False
            webpush_info = type("Mgr", (), {"count": lambda self: 0})()

        request = type("Request", (), {"user": MockUser()})()
        result = webpush_status(request)
        self.assertFalse(result["webpush_enabled"])
        self.assertEqual(result["webpush_subscription_count"], 0)


class HtmxMessageMiddlewareTest(TestCase):
    def test_htmx_request_gets_trigger_header(self):
        from django.http import HttpRequest
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib import messages

        request = HttpRequest()
        request.META["HTTP_HX_REQUEST"] = "true"
        request.session = {}
        setattr(request, "_messages", FallbackStorage(request))
        messages.success(request, "Test message")

        response = HttpResponse()

        middleware = HtmxMessageMiddleware(lambda req: response)
        middleware(request)

        self.assertIn("HX-Trigger", response)
        trigger = response["HX-Trigger"]
        self.assertIn("djangoMessages", trigger)


class QStashEmailBackendTest(TestCase):
    @patch("core.backends.send_background_email")
    def test_send_messages(self, mock_task):
        from core.backends import QStashEmailBackend
        from django.core.mail import EmailMultiAlternatives

        backend = QStashEmailBackend()
        email = EmailMultiAlternatives(
            subject="Test",
            body="Text body",
            from_email="test@example.com",
            to=["to@example.com"],
        )
        email.attach_alternative("<p>HTML body</p>", "text/html")
        count = backend.send_messages([email])
        self.assertEqual(count, 1)
        mock_task.delay.assert_called_once()

    @patch("core.backends.send_background_email")
    def test_empty_messages(self, mock_task):
        from core.backends import QStashEmailBackend

        backend = QStashEmailBackend()
        count = backend.send_messages([])
        self.assertEqual(count, 0)
        mock_task.delay.assert_not_called()


class NotificationsServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")

    def test_build_site_context(self):
        from core.services.notifications import build_site_context

        context = build_site_context()
        self.assertIn("site_url", context)
        self.assertIn("site_domain", context)
        self.assertIn("current_site", context)
        self.assertIn("domain", context["current_site"])
        self.assertIn("name", context["current_site"])

    def test_build_site_context_without_site(self):
        from core.services.notifications import build_site_context
        from django.contrib.sites.models import Site

        Site.objects.all().delete()
        context = build_site_context()
        self.assertIn("site_url", context)
        self.assertEqual(context["current_site"]["name"], "Papertrail")

    def test_build_expiry_webpush_payload(self):
        from core.services.notifications import build_expiry_webpush_payload

        payload = build_expiry_webpush_payload(3)
        self.assertEqual(payload["head"], "Record Expiry Alert")
        self.assertEqual(payload["body"], "You have 3 records expiring soon.")

    def test_build_expiry_webpush_payload_singular(self):
        from core.services.notifications import build_expiry_webpush_payload

        payload = build_expiry_webpush_payload(1)
        self.assertEqual(payload["body"], "You have 1 record expiring soon.")

    def test_user_can_receive_email_default_true(self):
        from core.services.notifications import _user_can_receive_email

        user_no_settings = User.objects.create_user(username="nosettings", password="pass")
        result = _user_can_receive_email(user_no_settings)
        self.assertTrue(result)

    def test_user_can_receive_email_true(self):
        from core.services.notifications import _user_can_receive_email

        self.user.settings.enable_email_notifications = True
        self.user.settings.save()
        result = _user_can_receive_email(self.user)
        self.assertTrue(result)

    def test_user_can_receive_email_false(self):
        from core.services.notifications import _user_can_receive_email

        self.user.settings.enable_email_notifications = False
        self.user.settings.save()
        result = _user_can_receive_email(self.user)
        self.assertFalse(result)

    def test_user_can_receive_push_no_settings(self):
        from core.services.notifications import _user_can_receive_push

        user_no_settings = User.objects.create_user(username="nosettings2", password="pass")
        result = _user_can_receive_push(user_no_settings)
        self.assertFalse(result)

    def test_user_can_receive_push_disabled(self):
        from core.services.notifications import _user_can_receive_push

        self.user.settings.enable_push_notifications = False
        self.user.settings.save()
        result = _user_can_receive_push(self.user)
        self.assertFalse(result)

    def test_build_expiry_email_context(self):
        from core.services.notifications import build_expiry_email_context

        context = build_expiry_email_context(
            user=self.user,
            records=[],
            remaining_count=0,
            total_records_count=0,
            auto_archive_msg="",
            action_url="https://example.com",
        )
        self.assertIn("user", context)
        self.assertIn("records", context)
        self.assertIn("action_url", context)
        self.assertIn("site_url", context)

    @patch("core.services.notifications.fire_single_webpush")
    @patch("core.services.notifications.send_email_notification")
    @patch("core.services.notifications._user_can_receive_push", return_value=True)
    def test_send_multi_channel_both(self, mock_can_push, mock_email, mock_push):
        from core.services.notifications import send_multi_channel_notification

        self.user.settings.enable_push_notifications = True
        self.user.settings.enable_email_notifications = True
        self.user.settings.save()
        send_multi_channel_notification(
            user=self.user,
            subject="Test",
            text_body="Text",
            html_body="<p>HTML</p>",
            webpush_payload={"head": "Test"},
            send_push=True,
            send_email=True,
        )
        mock_push.delay.assert_called_once()
        mock_email.assert_called_once()

    @patch("core.services.notifications.send_email_notification")
    def test_send_multi_channel_db_only(self, mock_email):
        from core.services.notifications import send_multi_channel_notification

        send_multi_channel_notification(
            user=self.user,
            subject="Test",
            text_body="Text",
            html_body="<p>HTML</p>",
            send_push=False,
            send_email=False,
            send_db=True,
            db_message="DB Test",
        )
        mock_email.assert_not_called()
        self.assertTrue(Notification.objects.filter(message="DB Test").exists())


class CoreTasksTest(TestCase):
    @patch("core.tasks.EmailMultiAlternatives")
    def test_send_background_email(self, mock_email_cls):
        from core.tasks import send_background_email

        send_background_email(
            subject="Test",
            message="Text body",
            from_email="from@example.com",
            recipient_list=["to@example.com"],
            html_message="<p>HTML</p>",
        )
        mock_email_cls.assert_called_once()
        mock_email_cls.return_value.send.assert_called_once()

    @patch("core.tasks.send_user_notification")
    def test_fire_single_webpush(self, mock_send):
        from core.tasks import fire_single_webpush

        user = User.objects.create_user(username="pushuser", password="pass")
        fire_single_webpush(user_id=user.id, payload={"head": "Test"}, ttl=1000)
        mock_send.assert_called_once()

    @patch("core.tasks.send_user_notification")
    def test_fire_single_webpush_user_not_found(self, mock_send):
        from core.tasks import fire_single_webpush

        fire_single_webpush(user_id=99999, payload={}, ttl=1000)
        mock_send.assert_not_called()
