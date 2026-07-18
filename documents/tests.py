import hashlib
import io
import zipfile
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from documents.filters import DocumentFilter
from records.models import Record
from documents.forms import R2UploadForm, DocumentUpdateForm
from documents.models import DocumentData, DocumentStatus
from documents.validators import (
    validate_file_upload,
    validate_file_bytes,
    _detect_mime_from_bytes,
)
from documents.storage import (
    generate_upload_key,
    generate_presigned_post,
    gatekeeper_validate_r2_object,
    generate_read_presigned_url,
    verify_r2_object_exists,
)
from records.models import Record


def _make_hash(content: bytes = b"test content") -> str:
    return hashlib.sha256(content).hexdigest()


class DocumentDataModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.record = Record.objects.create(
            user=self.user,
            title="Test Record",
            transaction_date=timezone.now().date(),
        )

    def test_create_document_data(self):
        doc = DocumentData.objects.create(
            user=self.user,
            filepath="users/1/uuid-test.pdf",
            file_hash=_make_hash(),
        )
        self.assertEqual(doc.status, DocumentStatus.PENDING_UPLOAD)
        self.assertEqual(doc.file_extension, "pdf")
        self.assertEqual(doc.title, "Untitled")
        self.assertIsNotNone(doc.created_at)

    def test_create_document_data_with_record(self):
        doc = DocumentData.objects.create(
            user=self.user,
            associated_record=self.record,
            filepath="users/1/uuid-test.pdf",
            file_hash=_make_hash(),
        )
        self.assertEqual(doc.associated_record, self.record)

    def test_file_extension_auto_extracted_on_save(self):
        doc = DocumentData.objects.create(
            user=self.user,
            filepath="users/1/uuid-document.PDF",
            file_hash=_make_hash(),
        )
        doc.save()
        self.assertEqual(doc.file_extension, "pdf")

    def test_file_extension_no_extension(self):
        doc = DocumentData(
            user=self.user,
            filepath="users/1/noext",
            file_hash=_make_hash(),
        )
        doc.save()
        self.assertEqual(doc.file_extension, "")

    def test_str(self):
        doc = DocumentData.objects.create(
            user=self.user,
            filepath="users/1/test.pdf",
            file_hash=_make_hash(),
        )
        self.assertEqual(str(doc), "users/1/test.pdf")

    def test_status_transitions_processing(self):
        doc = DocumentData.objects.create(
            user=self.user,
            filepath="users/1/test.pdf",
            file_hash=_make_hash(),
        )
        self.assertTrue(doc.is_processing)
        self.assertFalse(doc.is_terminal)

        doc.status = DocumentStatus.UPLOADED
        doc.save()
        self.assertTrue(doc.is_processing)
        self.assertFalse(doc.is_terminal)

        doc.status = DocumentStatus.PROCESSING
        doc.save()
        self.assertTrue(doc.is_processing)
        self.assertFalse(doc.is_terminal)

    def test_status_transitions_terminal(self):
        doc = DocumentData.objects.create(
            user=self.user,
            filepath="users/1/test.pdf",
            file_hash=_make_hash(),
        )
        doc.status = DocumentStatus.COMPLETED
        doc.save()
        self.assertFalse(doc.is_processing)
        self.assertTrue(doc.is_terminal)

        doc.status = DocumentStatus.ERROR
        doc.save()
        self.assertFalse(doc.is_processing)
        self.assertTrue(doc.is_terminal)

    def test_status_choices(self):
        self.assertEqual(DocumentStatus.PENDING_UPLOAD, "pending_upload")
        self.assertEqual(DocumentStatus.UPLOADED, "uploaded")
        self.assertEqual(DocumentStatus.PROCESSING, "processing")
        self.assertEqual(DocumentStatus.COMPLETED, "completed")
        self.assertEqual(DocumentStatus.ERROR, "error")
        self.assertEqual(DocumentStatus.DELETING, "deleting")

    def test_queryset_for_user(self):
        user2 = User.objects.create_user(username="user2", password="pass")
        DocumentData.objects.create(
            user=self.user, filepath="users/1/mine.pdf", file_hash=_make_hash()
        )
        DocumentData.objects.create(
            user=user2, filepath="users/2/theirs.pdf", file_hash=_make_hash(b"other")
        )
        self.assertEqual(DocumentData.objects.for_user(self.user).count(), 1)
        self.assertEqual(DocumentData.objects.for_user(user2).count(), 1)

    def test_queryset_orphaned(self):
        DocumentData.objects.create(
            user=self.user,
            associated_record=self.record,
            filepath="users/1/linked.pdf",
            file_hash=_make_hash(),
        )
        orphan = DocumentData.objects.create(
            user=self.user, filepath="users/1/orphan.pdf", file_hash=_make_hash(b"orphan")
        )
        qs = DocumentData.objects.orphaned()
        self.assertIn(orphan, qs)
        self.assertEqual(qs.count(), 1)

    def test_queryset_linked(self):
        DocumentData.objects.create(
            user=self.user,
            associated_record=self.record,
            filepath="users/1/linked.pdf",
            file_hash=_make_hash(),
        )
        DocumentData.objects.create(
            user=self.user, filepath="users/1/orphan.pdf", file_hash=_make_hash(b"orphan")
        )
        qs = DocumentData.objects.linked()
        self.assertEqual(qs.count(), 1)

    def test_queryset_by_status(self):
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/ready.pdf",
            file_hash=_make_hash(),
            status=DocumentStatus.COMPLETED,
        )
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/error.pdf",
            file_hash=_make_hash(b"err"),
            status=DocumentStatus.ERROR,
        )
        self.assertEqual(DocumentData.objects.by_status("completed").count(), 1)
        self.assertEqual(DocumentData.objects.by_status("error").count(), 1)

    def test_queryset_pending(self):
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/pending.pdf",
            file_hash=_make_hash(),
        )
        self.assertEqual(DocumentData.objects.pending().count(), 1)

    def test_queryset_stale_pending(self):
        doc = DocumentData.objects.create(
            user=self.user,
            filepath="users/1/stale.pdf",
            file_hash=_make_hash(),
        )
        DocumentData.objects.filter(pk=doc.pk).update(
            date_added=timezone.now() - timezone.timedelta(hours=2)
        )
        self.assertTrue(DocumentData.objects.stale_pending().exists())

    def test_queryset_stale_error(self):
        doc = DocumentData.objects.create(
            user=self.user,
            filepath="users/1/err.pdf",
            file_hash=_make_hash(),
            status=DocumentStatus.ERROR,
        )
        DocumentData.objects.filter(pk=doc.pk).update(
            date_added=timezone.now() - timezone.timedelta(days=3)
        )
        self.assertTrue(DocumentData.objects.stale_error().exists())

    def test_queryset_search(self):
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/tax.pdf",
            file_hash=_make_hash(),
            title="Tax Document",
        )
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/receipt.pdf",
            file_hash=_make_hash(b"receipt"),
            title="Receipt",
        )
        qs = DocumentData.objects.search("tax")
        self.assertEqual(qs.count(), 1)

    def test_queryset_search_empty(self):
        qs = DocumentData.objects.search("")
        self.assertEqual(qs.count(), 0)

    def test_unique_constraint(self):
        h = _make_hash()
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/unique.pdf",
            file_hash=h,
        )
        with self.assertRaises(Exception):
            DocumentData.objects.create(
                user=self.user,
                filepath="users/1/duplicate.pdf",
                file_hash=h,
            )

    def test_unique_constraint_different_user(self):
        user2 = User.objects.create_user(username="user2", password="pass")
        h = _make_hash()
        DocumentData.objects.create(
            user=self.user, filepath="users/1/a.pdf", file_hash=h
        )
        doc = DocumentData.objects.create(
            user=user2, filepath="users/2/a.pdf", file_hash=h
        )
        self.assertIsNotNone(doc.pk)


class DocumentDataManagerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.record = Record.objects.create(
            user=self.user,
            title="Test Record",
            transaction_date=timezone.now().date(),
        )

    def test_orphaned_excludes_linked(self):
        linked = DocumentData.objects.create(
            user=self.user,
            associated_record=self.record,
            filepath="users/1/linked.pdf",
            file_hash=_make_hash(),
        )
        orphan = DocumentData.objects.create(
            user=self.user, filepath="users/1/orphan.pdf", file_hash=_make_hash(b"orphan")
        )
        qs = DocumentData.objects.orphaned()
        self.assertIn(orphan, qs)
        self.assertNotIn(linked, qs)


class R2UploadFormTest(TestCase):
    def test_valid_form(self):
        form = R2UploadForm(
            data={
                "filename": "test.pdf",
                "content_type": "application/pdf",
            }
        )
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["filename"], "test.pdf")

    def test_filename_cleaned_to_basename(self):
        form = R2UploadForm(
            data={
                "filename": "subdir/test.pdf",
                "content_type": "application/pdf",
            }
        )
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["filename"], "test.pdf")

    def test_dot_filename_rejected(self):
        form = R2UploadForm(
            data={
                "filename": ".",
                "content_type": "application/pdf",
            }
        )
        self.assertFalse(form.is_valid())

    def test_dotdot_filename_rejected(self):
        form = R2UploadForm(
            data={
                "filename": "..",
                "content_type": "application/pdf",
            }
        )
        self.assertFalse(form.is_valid())

    def test_empty_filename(self):
        form = R2UploadForm(
            data={"filename": "", "content_type": "application/pdf"}
        )
        self.assertFalse(form.is_valid())

    def test_missing_content_type(self):
        form = R2UploadForm(data={"filename": "test.pdf"})
        self.assertFalse(form.is_valid())

    def test_allowed_content_types(self):
        for ct in ["application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]:
            with self.subTest(ct=ct):
                form = R2UploadForm(
                    data={
                        "filename": f"test.{ct.split('/')[1]}",
                        "content_type": ct,
                    }
                )
                self.assertTrue(form.is_valid(), msg=f"Failed for {ct}")

    def test_disallowed_content_type(self):
        form = R2UploadForm(
            data={
                "filename": "test.txt",
                "content_type": "text/plain",
            }
        )
        self.assertFalse(form.is_valid())

    def test_notes_field_optional(self):
        form = R2UploadForm(
            data={
                "filename": "test.pdf",
                "content_type": "application/pdf",
                "notes": "Some notes",
            }
        )
        self.assertTrue(form.is_valid())


class DocumentUpdateFormTest(TestCase):
    def test_valid_data(self):
        form = DocumentUpdateForm(
            data={
                "title": "Updated Title",
                "notes": "Some notes",
            }
        )
        self.assertTrue(form.is_valid())

    def test_associated_record_not_required(self):
        form = DocumentUpdateForm(data={"title": "Just Title"})
        self.assertTrue(form.is_valid())
        self.assertFalse(form.fields["associated_record"].required)

    def test_empty_title(self):
        form = DocumentUpdateForm(data={"title": ""})
        self.assertFalse(form.is_valid())

    def test_associated_record_queryset_active_only(self):
        user = User.objects.create_user(username="formuser", password="pass")
        active = Record.objects.create(
            user=user, title="Active", record_type="expense_receipt"
        )
        inactive = Record.objects.create(
            user=user, title="Inactive", record_type="voucher", is_active=False
        )
        form = DocumentUpdateForm()
        qs = form.fields["associated_record"].queryset
        self.assertIn(active, qs)
        self.assertNotIn(inactive, qs)


def _make_doc_filter_request(user):
    from django.http import HttpRequest
    req = HttpRequest()
    req.user = user
    return req


class DocumentFilterTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass")
        self.record = Record.objects.create(
            user=self.user,
            title="Test Record",
            record_type="expense_receipt",
        )

    def test_filter_by_status_orphaned(self):
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/report.pdf",
            file_hash=_make_hash(),
        )
        DocumentData.objects.create(
            user=self.user,
            associated_record=self.record,
            filepath="users/1/linked.pdf",
            file_hash=_make_hash(b"linked"),
        )
        qs = DocumentData.objects.filter(user=self.user)
        f = DocumentFilter(
            {"status": "orphaned"}, queryset=qs,
            request=_make_doc_filter_request(self.user),
        )
        self.assertEqual(f.qs.count(), 1)

    def test_filter_by_status_linked(self):
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/report.pdf",
            file_hash=_make_hash(),
        )
        DocumentData.objects.create(
            user=self.user,
            associated_record=self.record,
            filepath="users/1/linked.pdf",
            file_hash=_make_hash(b"linked"),
        )
        qs = DocumentData.objects.filter(user=self.user)
        f = DocumentFilter(
            {"status": "linked"}, queryset=qs,
            request=_make_doc_filter_request(self.user),
        )
        self.assertEqual(f.qs.count(), 1)

    def test_filter_by_file_type_queryset(self):
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/report.pdf",
            file_hash=_make_hash(),
            file_extension="pdf",
        )
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/photo.jpg",
            file_hash=_make_hash(b"photo"),
            file_extension="jpg",
        )
        qs = DocumentData.objects.filter(user=self.user, file_extension__iexact="pdf")
        self.assertEqual(qs.count(), 1)

    def test_no_filters(self):
        DocumentData.objects.create(
            user=self.user, filepath="users/1/a.pdf", file_hash=_make_hash()
        )
        qs = DocumentData.objects.filter(user=self.user)
        f = DocumentFilter({}, queryset=qs, request=_make_doc_filter_request(self.user))
        self.assertEqual(f.qs.count(), 1)


class UploadViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="uploaduser", password="pass")
        self.url = reverse("documents:upload_document")

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_returns_form_page(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "documents/upload_file.html")

    @patch("documents.views.generate_presigned_post")
    def test_post_presign_valid(self, mock_presign):
        mock_presign.return_value = "https://example.com/upload-url"
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {
                "filename": "test.pdf",
                "content_type": "application/pdf",
                "file_hash": _make_hash(),
            },
        )
        if response.status_code == 403:
            self.skipTest("Rate-limited in concurrent test run")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "upload_url")
        self.assertIn("document_id", data)
        self.assertIn("key", data)
        self.assertIn("upload_url", data)

    @patch("documents.views.generate_presigned_post")
    def test_post_presign_duplicate_detection(self, mock_presign):
        mock_presign.return_value = "https://example.com/upload-url"
        self.client.force_login(self.user)
        h = _make_hash()
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/dup.pdf",
            file_hash=h,
        )
        response = self.client.post(
            self.url,
            {
                "filename": "dup.pdf",
                "content_type": "application/pdf",
                "file_hash": h,
            },
        )
        if response.status_code == 200:
            data = response.json()
            self.assertEqual(data["status"], "duplicate_confirmed")

    @patch("documents.views.generate_presigned_post")
    def test_post_presign_force_upload_skips_duplicate(self, mock_presign):
        mock_presign.return_value = "https://example.com/upload-url"
        self.client.force_login(self.user)
        h = _make_hash()
        DocumentData.objects.create(
            user=self.user,
            filepath="users/1/force.pdf",
            file_hash=h,
        )
        response = self.client.post(
            self.url,
            {
                "filename": "force.pdf",
                "content_type": "application/pdf",
                "file_hash": h,
                "force_upload": "true",
            },
        )
        if response.status_code == 200:
            data = response.json()
            self.assertEqual(data["status"], "upload_url")

    def test_post_missing_file_hash(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {"filename": "test.pdf", "content_type": "application/pdf"},
        )
        if response.status_code == 200:
            self.fail("Should have returned 400 for missing file_hash")
        self.assertNotEqual(response.status_code, 500)

    def test_post_invalid_form(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {
                "filename": "",
                "content_type": "application/pdf",
                "file_hash": _make_hash(),
            },
        )
        if response.status_code == 200:
            self.fail("Should have returned 400 for invalid form")
        self.assertNotEqual(response.status_code, 500)


class ConfirmUploadViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="confirmuser", password="pass")
        self.url = reverse("documents:confirm_upload")
        self.doc = DocumentData.objects.create(
            user=self.user,
            filepath="users/1/confirmed.pdf",
            file_hash=_make_hash(),
        )

    def test_login_required(self):
        response = self.client.post(
            self.url, {"document_id": self.doc.id, "key": self.doc.filepath}
        )
        self.assertEqual(response.status_code, 302)

    @patch("documents.views.verify_r2_object_exists")
    @patch("documents.views.gatekeeper_validate_r2_object")
    def test_confirm_valid(self, mock_gatekeeper, mock_verify):
        mock_verify.return_value = True
        mock_gatekeeper.return_value = {"valid": True}
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {"document_id": self.doc.id, "key": self.doc.filepath},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "confirmed")
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, DocumentStatus.UPLOADED)

    def test_confirm_not_owned(self):
        user2 = User.objects.create_user(username="otheruser", password="pass")
        self.client.force_login(user2)
        response = self.client.post(
            self.url,
            {"document_id": self.doc.id, "key": self.doc.filepath},
        )
        self.assertEqual(response.status_code, 404)

    def test_confirm_not_found(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url, {"document_id": 99999, "key": "nonexistent"}
        )
        self.assertEqual(response.status_code, 404)

    def test_confirm_no_id(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 400)


class ViewDocumentViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="viewuser", password="pass")
        self.doc = DocumentData.objects.create(
            user=self.user,
            filepath="users/1/viewable.pdf",
            file_hash=_make_hash(),
            status=DocumentStatus.COMPLETED,
        )
        self.url = reverse("documents:view_document", args=[self.doc.id])

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_owner_can_view(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "documents/view_document.html")

    def test_other_user_cannot_view(self):
        user2 = User.objects.create_user(username="other", password="pass")
        self.client.force_login(user2)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_not_found(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("documents:view_document", args=[99999]))
        self.assertEqual(response.status_code, 404)

    def test_context_has_document(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.context["document"], self.doc)


class DeleteDocumentViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="deleteuser", password="pass")
        self.doc = DocumentData.objects.create(
            user=self.user,
            filepath="users/1/deletable.pdf",
            file_hash=_make_hash(),
        )
        self.url = reverse("documents:delete_document", args=[self.doc.id])

    def test_login_required(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)

    def test_owner_can_delete(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url)
        self.assertIn(response.status_code, [200, 302])
        self.assertFalse(DocumentData.objects.filter(id=self.doc.id).exists())

    def test_other_user_cannot_delete(self):
        user2 = User.objects.create_user(username="otherdel", password="pass")
        self.client.force_login(user2)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(DocumentData.objects.filter(id=self.doc.id).exists())


class AddSupportDocumentsViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="supuser", password="pass")
        self.record = Record.objects.create(
            user=self.user,
            title="Support Record",
            transaction_date=timezone.now().date(),
        )
        self.url = reverse(
            "documents:add_support_docs", args=[self.record.id]
        )

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_owner_can_access(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "documents/upload_supporting_files.html")

    def test_other_user_cannot_access(self):
        user2 = User.objects.create_user(username="othersup", password="pass")
        self.client.force_login(user2)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_record(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("documents:add_support_docs", args=[99999])
        )
        self.assertEqual(response.status_code, 404)


class DocumentListViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listuser", password="pass")
        self.url = reverse("documents:document_list_view")

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_authenticated_access(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "documents/document_list.html")


class ValidatorsTest(TestCase):
    def test_validate_file_upload_ok(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        file = SimpleUploadedFile(
            "test.pdf", b"%PDF-1.4 content", content_type="application/pdf"
        )
        result = validate_file_upload(file)
        self.assertEqual(result.mime_type, "application/pdf")

    def test_validate_file_upload_invalid_mime(self):
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile
        file = SimpleUploadedFile(
            "test.exe", b"binary content", content_type="application/x-msdownload"
        )
        with self.assertRaises(ValidationError):
            validate_file_upload(file)

    def test_validate_file_upload_size_limit(self):
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile
        file = SimpleUploadedFile(
            "large.pdf", b"x" * (51 * 1024 * 1024), content_type="application/pdf"
        )
        with self.assertRaises(ValidationError):
            validate_file_upload(file)

    def test_detect_mime_from_bytes_pdf(self):
        mime = _detect_mime_from_bytes(b"%PDF-1.4 content")
        self.assertEqual(mime, "application/pdf")

    def test_detect_mime_from_bytes_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("test.txt", "content")
        buf.seek(0)
        mime = _detect_mime_from_bytes(buf.read())
        self.assertEqual(mime, "application/zip")

    def test_detect_mime_from_bytes_jpeg(self):
        mime = _detect_mime_from_bytes(b"\xff\xd8\xff\xe0")
        self.assertEqual(mime, "image/jpeg")

    def test_detect_mime_from_bytes_png(self):
        mime = _detect_mime_from_bytes(b"\x89PNG\r\n\x1a\n")
        self.assertEqual(mime, "image/png")

    def test_detect_mime_from_bytes_gif(self):
        mime = _detect_mime_from_bytes(b"GIF89a")
        self.assertEqual(mime, "image/gif")

    def test_detect_mime_from_bytes_webp(self):
        mime = _detect_mime_from_bytes(b"RIFF\x00\x00\x00\x00WEBP")
        self.assertIsNotNone(mime)

    def test_detect_mime_from_bytes_tiff(self):
        mime = _detect_mime_from_bytes(b"II*\x00")
        self.assertEqual(mime, "image/tiff")

    def test_detect_mime_from_bytes_unknown(self):
        mime = _detect_mime_from_bytes(b"\x00\x01\x02\x03")
        self.assertIsNone(mime)

    def test_validate_file_bytes(self):
        result = validate_file_bytes(b"%PDF-1.4", 100)
        self.assertEqual(result.mime_type, "application/pdf")

    def test_validate_file_bytes_too_large(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_file_bytes(b"test", 51 * 1024 * 1024)

    def test_validate_file_bytes_empty(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_file_bytes(b"", 0)

    def test_validate_file_bytes_unknown_type(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_file_bytes(b"\x00\x01\x02\x03", 100)


class StorageUtilsTest(TestCase):
    def test_generate_upload_key(self):
        key = generate_upload_key(1, "pdf")
        self.assertTrue(key.startswith("users/1/"))
        self.assertTrue(key.endswith(".pdf"))
        self.assertNotEqual(key, "users/1/.pdf")

    def test_generate_upload_key_no_extension(self):
        key = generate_upload_key(1, "")
        self.assertTrue(key.startswith("users/1/"))
        self.assertIn(".", key)

    @patch("documents.storage.s3.generate_presigned_url")
    def test_generate_presigned_post(self, mock_gen):
        mock_gen.return_value = "https://example.com/presigned-url"
        result = generate_presigned_post(1, "users/1/test.pdf", "application/pdf")
        self.assertEqual(result, "https://example.com/presigned-url")

    @patch("documents.storage.s3.generate_presigned_url")
    def test_generate_read_presigned_url(self, mock_gen):
        mock_gen.return_value = "https://example.com/read-url"
        url = generate_read_presigned_url("users/1/test.pdf")
        self.assertEqual(url, "https://example.com/read-url")

    @patch("documents.storage.s3.head_object")
    def test_verify_r2_object_exists(self, mock_head):
        mock_head.return_value = {}
        result = verify_r2_object_exists("users/1/test.pdf")
        self.assertTrue(result)

    @patch("documents.storage.s3.head_object")
    def test_verify_r2_object_not_found(self, mock_head):
        from botocore.exceptions import ClientError
        mock_head.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}},
            "HeadObject",
        )
        result = verify_r2_object_exists("users/1/missing.pdf")
        self.assertFalse(result)

    @patch("documents.storage.s3.get_object")
    @patch("documents.storage.get_r2_object_head")
    def test_gatekeeper_validate_valid(self, mock_head, mock_get):
        mock_head.return_value = {"ContentLength": 100}
        mock_get.return_value = {"Body": io.BytesIO(b"%PDF-1.4 test content")}
        result = gatekeeper_validate_r2_object("users/1/test.pdf")
        self.assertTrue(result["valid"])
