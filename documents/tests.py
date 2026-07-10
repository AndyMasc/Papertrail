from django.test import TestCase
from django.contrib.auth.models import User
from django.test import Client
import json
from unittest.mock import patch


class UploadViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123"
        )

    def test_requires_login(self):
        response = self.client.post(
            "/documents/upload/",
            data=json.dumps({
                "filename": "test.pdf",
                "content_type": "application/pdf"
            }),
            content_type="application/json"
        )
    
        self.assertEqual(response.status_code, 302)  # redirect to login

    def test_missing_fields(self):
        self.client.login(username="testuser", password="testpass123")
    
        response = self.client.post(
            "/documents/upload/",
            data=json.dumps({
                "filename": "test.pdf"
                # missing content_type
            }),
            content_type="application/json"
        )
    
        self.assertEqual(response.status_code, 400)

    @patch("documents.views.initiate_r2_upload")
    def test_valid_upload_url(self, mock_generate):
        mock_generate.return_value = {
            "upload_url": "https://fake-upload-url",
            "key": "documents/test.pdf"
        }
    
        self.client.login(username="testuser", password="testpass123")
    
        response = self.client.post(
            "/documents/upload/",
            data=json.dumps({
                "filename": "test.pdf",
                "content_type": "application/pdf"
            }),
            content_type="application/json"
        )
    
        self.assertEqual(response.status_code, 200)
    
        data = response.json()
    
        self.assertIn("upload_url", data)
        self.assertIn("key", data)
        self.assertEqual(data["upload_url"], "https://fake-upload-url")