from tempfile import TemporaryDirectory
from unittest.mock import patch

import fitz
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.catalog.models import Chapter, Subject, Topic
from apps.content.models import Question
from apps.tenancy.models import Tenant
from .models import ScanImport, TenantOCRSettings
from .services import approve_extracted, process_scan


class ScanPipelineTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.tenant = Tenant.objects.create(name="Institute A", slug="institute-a")
        self.user = get_user_model().objects.create_user(
            username="teacher-a", email="teacher@example.test", password="testpass",
            tenant=self.tenant, role="tenant_admin")
        subject = Subject.objects.create(name="Physics", slug="physics")
        chapter = Chapter.objects.create(subject=subject, name="Mechanics")
        self.topic = Topic.objects.create(chapter=chapter, name="Center of Mass")
        pdf = fitz.open()
        pdf.new_page().insert_text((72, 72), "1. First question")
        pdf.new_page().insert_text((72, 72), "2. Second question")
        self.scan = ScanImport.objects.create(
            tenant=self.tenant, initiated_by=self.user, file_name="example.pdf",
            file=SimpleUploadedFile("example.pdf", pdf.tobytes(), content_type="application/pdf"),
            source="Licensed worksheet", license="Permission granted")
        pdf.close()

    def _response(self):
        return {"questions": [{"question_type": "single_correct", "stem": "A question?",
            "options": [{"id": "A", "text": "Yes"}], "answer": "A", "solution": "",
            "difficulty": 3, "topic_id": self.topic.pk, "exam": "", "year": "",
            "sitting": "", "figure_bboxes": []}]}

    @patch("apps.scans.services._request_page")
    def test_batches_save_progress_and_reviewable_tags(self, request_page):
        request_page.return_value = self._response()
        TenantOCRSettings.objects.create(tenant=self.tenant, page_batch_size=1)
        process_scan(self.scan)
        self.assertEqual((self.scan.status, self.scan.next_page, self.scan.extracted_count),
                         (ScanImport.Status.QUEUED, 1, 1))
        process_scan(self.scan)
        self.assertEqual((self.scan.status, self.scan.next_page, self.scan.extracted_count),
                         (ScanImport.Status.REVIEW, 2, 2))
        self.assertEqual(self.scan.extraction_payload[0]["tags"],
                         ["Physics", "Mechanics", "Center of Mass"])
        self.assertEqual([row["source_page"] for row in self.scan.extraction_payload], [1, 2])
        created = approve_extracted(self.scan, ["0"], {}, self.user)
        self.assertEqual(created[0].status, Question.Status.DRAFT)
        self.assertEqual(created[0].tags, ["Physics", "Mechanics", "Center of Mass"])
        self.assertEqual(created[0].tenant_id, self.tenant.pk)

    @patch("apps.scans.services._request_page")
    def test_failed_page_resumes_without_duplicate_rows(self, request_page):
        request_page.side_effect = [self._response(), RuntimeError("temporary OCR failure"), self._response()]
        process_scan(self.scan, page_batch=2)
        self.assertEqual((self.scan.status, self.scan.next_page, self.scan.extracted_count),
                         (ScanImport.Status.FAILED, 1, 1))
        process_scan(self.scan, page_batch=2)
        self.assertEqual((self.scan.status, self.scan.next_page, self.scan.extracted_count),
                         (ScanImport.Status.REVIEW, 2, 2))

    @patch("apps.scans.services._request_page")
    @patch("apps.scans.docling_adapter.extract_page_markdown")
    def test_local_docling_backend_needs_no_vision_call(self, markdown_pages, request_page):
        TenantOCRSettings.objects.create(tenant=self.tenant, backend="docling_local", page_batch_size=2)
        markdown_pages.return_value = [
            "# Center of Mass\n01. A body moves. Which option is correct?\n- (1) First\n- (2) Second\nExp. (2)\nPrinted explanation.",
            "# Center of Mass\n02. A second body moves. Which option is correct?\n- (1) First\n- (2) Second\nExp. (1)\nPrinted explanation.",
        ]
        process_scan(self.scan)
        request_page.assert_not_called()
        self.assertEqual((self.scan.status, self.scan.next_page, self.scan.extracted_count),
                         (ScanImport.Status.REVIEW, 2, 2))
        first = self.scan.extraction_payload[0]
        # (1)-(4) option labels are stored as A-D, like every other import path.
        self.assertEqual(first["options"], [{"id": "A", "text": "First"}, {"id": "B", "text": "Second"}])
        self.assertEqual((first["answer"], first["solution"]), ("B", "Printed explanation."))
        self.assertEqual(first["topic_id"], self.topic.pk)

    @patch("apps.scans.services._request_page")
    @patch("apps.scans.docling_adapter.extract_page_markdown")
    def test_local_backend_uses_shared_parser_formats(self, markdown_pages, request_page):
        from apps.learning.answering import is_correct
        TenantOCRSettings.objects.create(tenant=self.tenant, backend="docling_local", page_batch_size=2)
        markdown_pages.return_value = [
            # OCR output joined onto one line, inline options, short answer marker.
            "Center of Mass 1. A body moves along x. Its centre of mass is (A) fixed (B) moving (C) at origin "
            "(D) undefined Ans: b 2. Which of these are vectors here? (A) mass (B) velocity (C) speed (D) momentum Answer: B, D",
            "Q3. Find the value of g in metres per second squared\nAnswer: 9.8",
        ]
        process_scan(self.scan)
        request_page.assert_not_called()
        rows = self.scan.extraction_payload
        self.assertEqual([(r["question_type"], r["answer"]) for r in rows],
                         [("single_correct", "B"), ("multi_correct", '["B", "D"]'), ("numerical", "9.8")])
        self.assertEqual(rows[0]["options"][3], {"id": "D", "text": "undefined"})
        self.assertEqual([r["source_page"] for r in rows], [1, 1, 2])
        created = approve_extracted(self.scan, ["0", "1", "2"], {"2": {"topic_id": self.topic.pk}}, self.user)
        self.assertEqual(len(created), 3)
        by_type = {q.question_type: q for q in created}
        self.assertTrue(is_correct(by_type["multi_correct"], ["B", "D"]))
        self.assertFalse(is_correct(by_type["multi_correct"], ["B"]))
        self.assertTrue(is_correct(by_type["single_correct"], ["B"]))
        self.assertTrue(is_correct(by_type["numerical"], ["9.8"]))
