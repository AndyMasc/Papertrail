import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase


class UploadViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser", password="testpass123"
        )

    def test_requires_login(self):
        response = self.client.post(
            "/documents/upload/",
            data=json.dumps(
                {"filename": "test.pdf", "content_type": "application/pdf"}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 302)  # redirect to login

    def test_missing_fields(self):
        self.client.login(username="testuser", password="testpass123")

        response = self.client.post(
            "/documents/upload/",
            data=json.dumps(
                {
                    "filename": "test.pdf"
                    # missing content_type
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    @patch("documents.views.generate_presigned_post")
    @patch("documents.views.verify_r2_object_exists")
    def test_valid_upload_url(self, mock_verify, mock_generate):
        mock_generate.return_value = "https://fake-upload-url"
        mock_verify.return_value = True

        self.client.login(username="testuser", password="testpass123")

        response = self.client.post(
            "/documents/upload/",
            data=json.dumps(
                {
                    "file_hash": "abc123",
                    "filename": "test.pdf",
                    "content_type": "application/pdf",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertIn("upload_url", data)
        self.assertIn("key", data)
        self.assertEqual(data["upload_url"], "https://fake-upload-url")
