from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from records.models import Record


# Create your tests here.
class HomeViewTest(TestCase):
    def test_home_status(self):
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)

    def test_home_template(self):
        response = self.client.get(reverse("core:home"))
        self.assertTemplateUsed(response, "core/home.html")

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
