"""End-to-end checks for admin Excel and PDF question uploads.

Drives the real admin pages (add form -> Process & review -> commit) and checks
that committed questions land under the right exam and subject.
"""
import io
import shutil
import tempfile

import pymupdf as fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from openpyxl import load_workbook

from apps.accounts.models import User
from apps.catalog.models import Exam
from apps.content.models import Question
from apps.features.models import FeatureEntitlement, TenantExam
from apps.imports.models import ContentJob
from apps.imports.workbooks import workbook_bytes
from apps.tenancy.models import Tenant

TMP_MEDIA = tempfile.mkdtemp()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def q(**values):
    base = {"kind": "PYQ", "year": "2024", "chapter": "General", "topic": "Basics", "difficulty": "2",
            "question_type": "single_correct", "option_a": "one", "option_b": "two", "option_c": "three",
            "option_d": "four", "answer": "A", "source": "Upload test", "license": "Test only"}
    base.update(values)
    return base


def entitle(tenant, *keys):
    for key in keys:
        FeatureEntitlement.objects.update_or_create(tenant=tenant, key=key,
            defaults={"entitled": True, "enabled_override": True})


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class AdminUploadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = Tenant.objects.get(is_master=True)
        cls.institute = Tenant.objects.create(name="Upload Institute", slug="upload-inst")
        entitle(cls.master, "bulk_question_import", "tests", "scan_import", "practice")
        entitle(cls.institute, "bulk_question_import", "tests", "practice")
        cls.exams = {slug: Exam.objects.get_or_create(slug=slug, defaults={"name": name, "active": True})[0]
                     for slug, name in [("jee-main", "JEE Main"), ("jee-advanced", "JEE Advanced"), ("neet", "NEET")]}
        cls.root = User.objects.create_superuser(username="root", email="root@example.test",
            password="Root-upload-password-27", email_verified_at=timezone.now())
        cls.inst_admin = User.objects.create_user(username="instadmin", email="admin@inst.example.test",
            password="Inst-upload-password-27", tenant=cls.institute, role="tenant_admin", is_staff=True,
            email_verified_at=timezone.now())

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def client_for(self, user):
        client = Client(HTTP_HOST="localhost")
        client.force_login(user)
        return client

    def upload(self, client, tenant, kind, name, content, ctype=XLSX, **extra):
        data = {"tenant": tenant.pk, "kind": kind, "source": "Upload test", "license": "Test only",
                "file": SimpleUploadedFile(name, content, content_type=ctype), **extra}
        response = client.post("/admin/imports/contentjob/add/", data)
        return response, ContentJob.objects.order_by("-pk").first()

    def process(self, client, job):
        client.post(f"/admin/imports/contentjob/{job.pk}/review/", {"action": "process"})
        job.refresh_from_db()
        return job

    def commit(self, client, job):
        client.post(f"/admin/imports/contentjob/{job.pk}/review/", {"action": "commit", "reviewed": "yes"})
        job.refresh_from_db()
        return job

    # ---------- Excel ----------

    def test_template_downloads(self):
        client = self.client_for(self.root)
        for kind in ["questions", "exams", "tests", "materials"]:
            response = client.get(f"/admin/imports/contentjob/template/{kind}/")
            self.assertEqual(response.status_code, 200, kind)
            self.assertEqual(response["Content-Type"], XLSX)

    def test_superuser_excel_pyqs_segregated_by_exam_and_subject(self):
        rows = [
            q(source_id="m-phy", exam="jee-main", subject="Physics", stem="JEE Main physics upload"),
            q(source_id="m-chem", exam="jee-main", subject="Chemistry", stem="JEE Main chemistry upload"),
            q(source_id="a-math", exam="jee-advanced", subject="Mathematics", stem="Advanced maths multi upload",
              question_type="multi_correct", answer="A,C"),
            q(source_id="a-num", exam="jee-advanced", subject="Physics", stem="Advanced numerical upload",
              question_type="numerical", answer="9.8", option_a="", option_b="", option_c="", option_d=""),
            q(source_id="n-bio", exam="neet", subject="Biology", stem="NEET biology upload"),
        ]
        client = self.client_for(self.root)
        response, job = self.upload(client, self.master, "questions", "pyqs.xlsx", workbook_bytes("questions", rows))
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        job = self.process(client, job)
        self.assertEqual(job.status, "review", job.errors)
        self.assertFalse(Question.objects.filter(stem__endswith="upload").exists(), "preview must not write")
        job = self.commit(client, job)
        self.assertEqual(job.status, "completed", job.errors)
        self.assertEqual(job.created_count, 5)
        placed = {qn.stem: (qn.paper.exam.slug, qn.topic.chapter.subject.name, qn.status, qn.tenant_id)
                  for qn in Question.objects.filter(stem__endswith="upload")}
        self.assertEqual(placed["JEE Main physics upload"], ("jee-main", "Physics", "draft", None))
        self.assertEqual(placed["JEE Main chemistry upload"], ("jee-main", "Chemistry", "draft", None))
        self.assertEqual(placed["Advanced maths multi upload"], ("jee-advanced", "Mathematics", "draft", None))
        self.assertEqual(placed["Advanced numerical upload"], ("jee-advanced", "Physics", "draft", None))
        self.assertEqual(placed["NEET biology upload"], ("neet", "Biology", "draft", None))
        # Re-committing / re-uploading the same file must not duplicate.
        _, again = self.upload(client, self.master, "questions", "pyqs.xlsx", workbook_bytes("questions", rows))
        self.commit(client, self.process(client, again))
        self.assertEqual(Question.objects.filter(stem__endswith="upload").count(), 5)

        # After publishing, learners see each question only under its own exam + subject.
        Question.objects.filter(stem__endswith="upload").update(status="published")
        TenantExam.objects.get_or_create(tenant=self.master, exam=self.exams["jee-main"], defaults={"enabled": True})
        learner = User.objects.create_user(username="learner", email="learner@example.test",
            password="Learner-upload-password-27", email_verified_at=timezone.now())
        page = self.client_for(learner).get(
            f"/practice/?exam={self.exams['jee-main'].pk}&subject=Physics&view=browse")
        self.assertContains(page, "JEE Main physics upload")
        self.assertNotContains(page, "JEE Main chemistry upload")
        self.assertNotContains(page, "NEET biology upload")

    def test_excel_bad_rows_block_whole_batch(self):
        rows = [q(source_id="ok", exam="jee-main", subject="Physics", stem="Good row bad batch"),
                q(source_id="bad-ans", exam="jee-main", subject="Physics", stem="Answer E row", answer="E"),
                q(source_id="no-subj", exam="neet", subject="", stem="Missing subject row")]
        client = self.client_for(self.root)
        _, job = self.upload(client, self.master, "questions", "bad.xlsx", workbook_bytes("questions", rows))
        job = self.process(client, job)
        self.assertEqual(job.status, "failed")
        self.assertEqual(len(job.errors), 2, job.errors)
        self.assertFalse(Question.objects.filter(stem="Good row bad batch").exists())

    def test_excel_unknown_exam_slug_for_superuser(self):
        """Documents current behaviour: a typo'd exam slug silently creates a new exam."""
        rows = [q(source_id="typo", exam="jee-mains", subject="Physics", stem="Typo exam row")]
        client = self.client_for(self.root)
        _, job = self.upload(client, self.master, "questions", "typo.xlsx", workbook_bytes("questions", rows))
        self.commit(client, self.process(client, job))
        self.assertTrue(Exam.objects.filter(slug="jee-mains").exists())

    def test_institute_admin_excel_practice_ok_pyq_rejected(self):
        client = self.client_for(self.inst_admin)
        rows = [q(source_id="p1", kind="PRACTICE", exam="", subject="Chemistry", stem="Institute practice upload")]
        response, job = self.upload(client, self.institute, "questions", "inst.xlsx", workbook_bytes("questions", rows))
        self.assertEqual(response.status_code, 302)
        job = self.commit(client, self.process(client, job))
        self.assertEqual(job.status, "completed", job.errors)
        made = Question.objects.get(stem="Institute practice upload")
        self.assertEqual((made.tenant_id, made.kind, made.topic.chapter.subject.name), (self.institute.pk, "PRACTICE", "Chemistry"))

        rows = [q(source_id="p2", exam="jee-main", subject="Physics", stem="Institute PYQ attempt")]
        _, job = self.upload(client, self.institute, "questions", "inst-pyq.xlsx", workbook_bytes("questions", rows))
        job = self.process(client, job)
        self.assertEqual(job.status, "failed")
        self.assertIn("platform administrator", job.errors[0])

    def test_excel_timed_test_upload(self):
        base = {"test_name": "Upload physics test", "exam": "jee-main", "duration_minutes": "30",
                "section": "Single correct", "correct_marks": "4", "incorrect_marks": "-1"}
        rows = [{**q(source_id=f"t{i}", subject="Physics", stem=f"Timed upload {i}"), **base} for i in range(3)]
        client = self.client_for(self.inst_admin)
        _, job = self.upload(client, self.institute, "tests", "test.xlsx", workbook_bytes("tests", rows))
        job = self.commit(client, self.process(client, job))
        self.assertEqual(job.status, "completed", job.errors)
        from apps.assessments.models import TimedTest
        test = TimedTest.objects.get(name="Upload physics test")
        self.assertEqual((test.exam.slug, len(test.questions), test.active), ("jee-main", 3, False))

    def test_wrong_extension_rejected(self):
        client = self.client_for(self.root)
        response, _ = self.upload(client, self.master, "questions", "q.csv", b"a,b\n", ctype="text/csv")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a .xlsx file")

    # ---------- PDF ----------

    def make_pdf(self, pages):
        doc = fitz.open()
        for text in pages:
            page = doc.new_page()
            page.insert_text((50, 60), text, fontsize=10)
        raw = doc.tobytes()
        doc.close()
        return raw

    def test_pdf_to_excel_then_reimport_by_exam_and_subject(self):
        pdf = self.make_pdf([
            "1. A body of mass 2 kg moves at 3 m/s. Its kinetic energy is\n(A) 9 J\n(B) 6 J\n(C) 3 J\n(D) 18 J\nAnswer: A\n"
            "2. Which gas is evolved when zinc reacts with dilute HCl?\n(A) Oxygen\n(B) Hydrogen\n(C) Chlorine\n(D) Nitrogen\nAnswer: B",
            "3. The powerhouse of the cell is the\n(A) Nucleus\n(B) Ribosome\n(C) Mitochondrion\n(D) Golgi body\nAnswer: C",
        ])
        client = self.client_for(self.root)
        response, job = self.upload(client, self.master, "pdf", "paper.pdf", pdf, ctype="application/pdf")
        self.assertEqual(response.status_code, 302)
        job = self.process(client, job)
        self.assertEqual(job.status, "review", job.errors)
        self.assertEqual(job.page_count, 2)
        self.assertEqual(len(job.rows), 3, job.rows)
        self.assertEqual([r["answer"] for r in job.rows], ["A", "B", "C"])
        self.assertEqual(job.rows[1]["option_b"], "Hydrogen")
        # PDF extraction never assigns exam/subject; those must be filled during review.
        self.assertTrue(all(not r["exam"] and not r["subject"] for r in job.rows))
        # PDF jobs cannot be committed directly.
        self.assertEqual(self.commit(client, job).status, "review")

        sheet = client.get(f"/admin/imports/contentjob/{job.pk}/download/")
        self.assertEqual(sheet.status_code, 200)
        book = load_workbook(io.BytesIO(sheet.content))
        self.assertIn("Source pages", book.sheetnames)
        ws = book["Questions"]
        header = [c.value for c in ws[1]]
        fill = [("jee-main", "Physics"), ("jee-main", "Chemistry"), ("neet", "Biology")]
        for row, (exam, subject) in zip(ws.iter_rows(min_row=2), fill):
            values = dict(zip(header, row))
            values["kind"].value = "PYQ"
            values["exam"].value, values["subject"].value, values["year"].value = exam, subject, "2023"
            values["chapter"].value, values["topic"].value = "From PDF", "From PDF"
        out = io.BytesIO()
        book.save(out)

        _, reimport = self.upload(client, self.master, "questions", "reviewed.xlsx", out.getvalue())
        reimport = self.commit(client, self.process(client, reimport))
        self.assertEqual(reimport.status, "completed", reimport.errors)
        placed = sorted((qn.paper.exam.slug, qn.topic.chapter.subject.name)
                        for qn in Question.objects.filter(source="Upload test", topic__name="From PDF"))
        self.assertEqual(placed, sorted(fill))

    def test_scanned_pdf_without_ocr_is_flagged(self):
        doc = fitz.open()
        doc.new_page()  # blank page stands in for an image-only scan
        raw = doc.tobytes()
        client = self.client_for(self.root)
        _, job = self.upload(client, self.master, "pdf", "scan.pdf", raw, ctype="application/pdf")
        job = self.process(client, job)
        self.assertEqual(job.rows, [])
        self.assertIn("needs configured OCR", job.pages[0]["warnings"])

    def test_institute_pdf_blocked_without_scan_import_feature(self):
        client = self.client_for(self.inst_admin)
        pdf = self.make_pdf(["1. Some question text here\n(A) x\n(B) y"])
        response, _ = self.upload(client, self.institute, "pdf", "inst.pdf", pdf, ctype="application/pdf")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ContentJob.objects.filter(tenant=self.institute, kind="pdf").exists())
