from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth.models import User
from records.models import Record
from django.core.cache import cache


# Create your tests here.
class HomeViewTest(TestCase):
    def test_home_status(self):
        response = self.client.get(reverse("core:landing_page"))
        self.assertEqual(response.status_code, 200)

    def test_home_template(self):
        response = self.client.get(reverse("core:home"))
        self.assertTemplateUsed(response, "core/landing_page.html")

class RecordAccessTest(TestCase):
    def test_record_access(self):
        user1 = User.objects.create_user(username="user1", password="pass")
        user2 = User.objects.create_user(username="user2", password="pass")
        
        Record.objects.create(user=user1, title="A")
        Record.objects.create(user=user2, title="B")
        self.client.force_login(user1)
        
        response = self.client.get(reverse("core:dashboard"))
        records = response.context["records"]
        
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "A")

        titles = [record.title for record in records]
        self.assertNotIn("B", titles)


class AllauthRateLimitTests(TestCase):

    def setUp(self):
        # clear the cache before each test to ensure a clean slate
        cache.clear()

    @override_settings(CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        },
    },
    ACCOUNT_RATE_LIMITS={"signup": "1/m"}
)
    def test_signup_rate_limiting(self):
        signup_url = reverse("account_signup")
        payload = {
            "email": "testuser@example.com",
        }

        # First request should pass through normally (e.g., Form errors or redirect)
        response1 = self.client.post(signup_url, payload)
        self.assertNotEqual(response1.status_code, 429)

        # Second request within the same minute must trigger the rate limit
        response2 = self.client.post(signup_url, payload)
        self.assertEqual(response2.status_code, 429)
        
        # Verify it renders the expected template
        self.assertTemplateUsed(response2, "429.html")