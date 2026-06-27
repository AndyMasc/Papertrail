from django.test import TestCase
from django.urls import reverse


# Create your tests here.
class HomeViewTest(TestCase):
    def test_home_status(self):
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)

    def test_home_template(self):
        response = self.client.get(reverse("core:home"))
        self.assertTemplateUsed(response, "core/home.html")
