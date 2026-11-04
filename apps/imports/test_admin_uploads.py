"""End-to-end checks for admin Excel and PDF question uploads.

Drives the real admin pages (add form -> Process & review -> commit) and checks
that committed questions land under the right exam and subject.
"""
import io
import shutil
import tempfile
from unittest.mock import patch

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

    def test_excel_unknown_exam_is_rejected_with_suggestion(self):
        rows = [q(source_id="typo", exam="jee-mains", subject="Physics", stem="Typo exam row"),
                q(source_id="byname", exam="JEE Advanced", subject="Physics", stem="Exam given by name")]
        client = self.client_for(self.root)
        _, job = self.upload(client, self.master, "questions", "typo.xlsx", workbook_bytes("questions", rows))
        job = self.process(client, job)
        self.assertEqual(job.status, "failed")
        self.assertEqual(len(job.errors), 1, job.errors)
        self.assertIn('Did you mean "jee-main"', job.errors[0])
        self.assertFalse(Exam.objects.filter(slug="jee-mains").exists())

    def test_exams_import_still_creates_exams(self):
        client = self.client_for(self.root)
        _, job = self.upload(client, self.master, "exams", "exams.xlsx",
                             workbook_bytes("exams", [{"exam": "bitsat", "name": "BITSAT", "active": "true"}]))
        job = self.commit(client, self.process(client, job))
        self.assertEqual(job.status, "completed", job.errors)
        self.assertTrue(Exam.objects.get(slug="bitsat").active)

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
        self.assertContains(response, "Upload a .xlsx or .zip file")

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

    def scanned_pdf(self, pages=1):
        doc = fitz.open()
        for _ in range(pages):
            doc.new_page()  # blank page stands in for an image-only scan
        return doc.tobytes()

    def test_scanned_pdf_uses_local_ocr(self):
        ocr_text = "1. The powerhouse of the cell is the (A) Nucleus (B) Ribosome (C) Mitochondrion (D) Golgi body Answer: C"
        client = self.client_for(self.root)
        _, job = self.upload(client, self.master, "pdf", "scan.pdf", self.scanned_pdf(), ctype="application/pdf")
        with patch("apps.scans.docling_adapter.extract_page_markdown", return_value=[ocr_text]) as ocr:
            job = self.process(client, job)
        self.assertEqual(ocr.call_args.kwargs, {"formulas": False})
        self.assertEqual(job.status, "review", job.errors)
        self.assertEqual(len(job.rows), 1)
        row = job.rows[0]
        self.assertEqual((row["option_c"], row["answer"]), ("Mitochondrion", "C"))
        self.assertIn("Local OCR", row["warnings"])

    def test_scanned_pages_are_limited_per_admin_click(self):
        client = self.client_for(self.root)
        _, job = self.upload(client, self.master, "pdf", "scan5.pdf", self.scanned_pdf(5), ctype="application/pdf")
        with patch("apps.scans.docling_adapter.extract_page_markdown", side_effect=lambda raw, formulas: [""] * fitz.open(stream=raw).page_count):
            job = self.process(client, job)
            self.assertEqual((job.status, job.next_page), ("queued", 3))
            job = self.process(client, job)
        self.assertEqual((job.status, job.next_page), ("review", 5))

    def test_scanned_pdf_reports_missing_local_ocr(self):
        client = self.client_for(self.root)
        _, job = self.upload(client, self.master, "pdf", "scan.pdf", self.scanned_pdf(), ctype="application/pdf")
        with patch("apps.scans.docling_adapter.extract_page_markdown",
                   side_effect=RuntimeError("Docling OCR is selected but not installed.")):
            job = self.process(client, job)
        self.assertEqual(job.status, "review")
        self.assertIn("requirements-ocr.txt", job.pages[0]["warnings"])

    def test_institute_pdf_allowed_with_question_import_but_vision_needs_scan_import(self):
        client = self.client_for(self.inst_admin)
        pdf = self.make_pdf(["1. Some question text here\n(A) x\n(B) y\nAnswer: B"])
        response, job = self.upload(client, self.institute, "pdf", "inst.pdf", pdf, ctype="application/pdf")
        self.assertEqual(response.status_code, 302)
        job = self.process(client, job)
        self.assertEqual((job.status, job.rows[0]["answer"]), ("review", "B"))
        response, _ = self.upload(client, self.institute, "pdf", "inst-ocr.pdf", pdf, ctype="application/pdf", use_ocr="on")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ContentJob.objects.filter(tenant=self.institute, kind="pdf").count(), 1)

    def test_two_column_pdf_reads_left_column_first(self):
        doc = fitz.open()
        page = doc.new_page()
        left = ["1. Left column first question text", "(A) alpha", "(B) beta", "Answer: A", "",
                "2. Left column second question", "(A) gamma", "(B) delta", "Answer: B"]
        right = ["3. Right column third question", "(A) epsilon", "(B) zeta", "Answer: B", "",
                 "4. Right column fourth question", "(A) eta", "(B) theta", "Answer: A"]
        page.insert_textbox(fitz.Rect(40, 60, 290, 760), "\n".join(left * 3), fontsize=10)
        page.insert_textbox(fitz.Rect(320, 60, 570, 760), "\n".join(right * 3), fontsize=10)
        client = self.client_for(self.root)
        _, job = self.upload(client, self.master, "pdf", "cols.pdf", doc.tobytes(), ctype="application/pdf")
        job = self.process(client, job)
        stems = [r["stem"] for r in job.rows]
        self.assertEqual(stems[:2], ["Left column first question text", "Left column second question"])
        self.assertIn("Right column third question", stems)
        self.assertEqual(job.rows[0]["option_b"], "beta")


class SplitQuestionsTests(TestCase):
    class Job:
        pk, source, license = 1, "s", "l"

    def parse(self, text):
        from apps.imports.pdf_excel import split_questions
        return split_questions(text, 1, self.Job)

    def test_inline_options(self):
        rows = self.parse("1. Kinetic energy of 2 kg at 3 m/s is\n(A) 9 J  (B) 6 J  (C) 3 J  (D) 18 J\nAns: A")
        self.assertEqual([rows[0][f"option_{x}"] for x in "abcd"], ["9 J", "6 J", "3 J", "18 J"])
        self.assertEqual(rows[0]["answer"], "A")

    def test_q_prefix_and_short_answer(self):
        rows = self.parse("Q1. Which gas is evolved here today?\na) Oxygen\nb) Hydrogen\nAns: b\n"
                          "Q2. Another question with enough text\na) x\nb) y\nAns. (a)")
        self.assertEqual([(r["option_b"], r["answer"]) for r in rows], [("Hydrogen", "B"), ("y", "A")])

    def test_numeric_option_labels_map_to_letters(self):
        rows = self.parse("1. The powerhouse of the cell is\n(1) Nucleus\n(2) Ribosome\n(3) Mitochondrion\n(4) Golgi\nAnswer: 3")
        self.assertEqual((rows[0]["question_type"], rows[0]["option_c"], rows[0]["answer"]),
                         ("single_correct", "Mitochondrion", "C"))

    def test_multi_correct_numerical_and_solution(self):
        rows = self.parse("1. Pick the even numbers here\n(A) 2 (B) 3 (C) 4 (D) 5\nAnswer: A, C\n"
                          "2. Value of g in m/s2 is approximately\nAnswer: 9.8\nSolution: Standard gravity.")
        self.assertEqual((rows[0]["question_type"], rows[0]["answer"]), ("multi_correct", "A,C"))
        self.assertEqual((rows[1]["question_type"], rows[1]["answer"], rows[1]["solution"]),
                         ("numerical", "9.8", "Standard gravity."))

    def test_ocr_single_line_with_several_questions(self):
        rows = self.parse("1. First question on one line (A) a1 (B) b1 Answer: B 2. Second question here (A) a2 (B) b2 Answer: A")
        self.assertEqual([(r["stem"], r["option_b"], r["answer"]) for r in rows],
                         [("First question on one line", "b1", "B"), ("Second question here", "b2", "A")])

    def test_numbers_inside_stem_do_not_split(self):
        rows = self.parse("1. A car travels 5. km in 2: minutes; find speed\n(A) 1 (B) 2\nAnswer: A")
        self.assertEqual(len(rows), 1)
