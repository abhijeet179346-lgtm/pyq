"""Question images: admin upload, learner access control, rendering, timed snapshots and zip imports."""
import io
import shutil
import tempfile
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.models import model_to_dict
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.assessments.models import TimedTest
from apps.catalog.models import Chapter, Exam, Paper, Subject, Topic
from apps.content.models import Question
from apps.features.models import FeatureEntitlement, TenantExam
from apps.imports.models import ContentJob
from apps.imports.workbooks import workbook_bytes
from apps.tenancy.models import Tenant

MEDIA = tempfile.mkdtemp()


def png(colour=(200, 30, 30), size=(40, 30)):
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


@override_settings(MEDIA_ROOT=MEDIA)
class FigureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = Tenant.objects.get(is_master=True)
        for key in ("practice", "global_pyq_bank", "tests", "bulk_question_import"):
            FeatureEntitlement.objects.update_or_create(tenant=cls.master, key=key, defaults={"entitled": True, "enabled_override": True})
        cls.exam = Exam.objects.create(slug="fig-exam", name="Figure exam", active=True)
        TenantExam.objects.create(tenant=cls.master, exam=cls.exam, enabled=True)
        cls.paper = Paper.objects.create(exam=cls.exam, year=2024)
        subject = Subject.objects.create(name="Optics", slug="optics")
        cls.topic = Topic.objects.create(chapter=Chapter.objects.create(subject=subject, name="Rays"), name="Mirrors")
        cls.question = Question.objects.create(kind="PYQ", paper=cls.paper, topic=cls.topic, stem="Use the ray diagram",
            options=[{"id": "A", "text": "x"}, {"id": "B", "text": "y"}], answer="A", source="Fixture", license="Fixture",
            status="published")
        cls.root = User.objects.create_superuser(username="figroot", email="figroot@example.test", password="Fig-root-password-27",
            email_verified_at=timezone.now())
        cls.learner = User.objects.create_user(username="figlearner", email="figlearner@example.test",
            password="Fig-learner-password-27", email_verified_at=timezone.now())
        cls.other = User.objects.create_user(username="figother", email="figother@example.test",
            password="Fig-learner-password-27", email_verified_at=timezone.now())

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def client_for(self, user):
        client = Client(HTTP_HOST="localhost")
        client.force_login(user)
        return client

    def admin_save(self, client, question, **extra):
        data = {k: ("" if v is None else v) for k, v in model_to_dict(question, exclude=["figure_paths", "id"]).items()}
        data["options"] = '[{"id": "A", "text": "x"}, {"id": "B", "text": "y"}]'
        data["tags"] = "[]"
        for prefix in ("study_links",):
            data.update({f"{prefix}-TOTAL_FORMS": "0", f"{prefix}-INITIAL_FORMS": "0"})
        data.update(extra)
        return client.post(f"/admin/content/question/{question.pk}/change/", data)

    def attach(self, *images):
        client = self.client_for(self.root)
        files = [SimpleUploadedFile(f"fig{i}.png", raw, content_type="image/png") for i, raw in enumerate(images)]
        response = self.admin_save(client, self.question, add_figures=files)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and [response.context["adminform"].form.errors, [f.formset.errors for f in response.context["inline_admin_formsets"]], [f.formset.non_form_errors() for f in response.context["inline_admin_formsets"]]])
        self.question.refresh_from_db()
        return client

    def test_admin_uploads_previews_and_removes_images(self):
        client = self.attach(png(), png((0, 0, 200)))
        self.assertEqual(len(self.question.figure_paths), 2)
        preview = client.get(f"/admin/content/question/{self.question.pk}/figures/0/")
        self.assertEqual((preview.status_code, preview["Content-Type"]), (200, "image/png"))
        self.assertContains(client.get(f"/admin/content/question/{self.question.pk}/change/"), "Image 2")
        response = self.admin_save(client, self.question, remove_figures=["0"])
        self.assertEqual(response.status_code, 302)
        self.question.refresh_from_db()
        self.assertEqual(len(self.question.figure_paths), 1)

    def test_admin_rejects_non_images(self):
        client = self.client_for(self.root)
        fake = SimpleUploadedFile("evil.png", b"<script>alert(1)</script>", content_type="image/png")
        response = self.admin_save(client, self.question, add_figures=[fake])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not a readable image")
        self.question.refresh_from_db()
        self.assertEqual(self.question.figure_paths, [])

    def test_learner_sees_images_only_for_visible_questions(self):
        self.attach(png())
        learner = self.client_for(self.learner)
        page = learner.get(f"/practice/questions/{self.question.pk}/")
        url = f"/practice/questions/{self.question.pk}/figures/0/"
        self.assertContains(page, f'src="{url}"')
        self.assertEqual(learner.get(url).status_code, 200)
        self.assertEqual(learner.get(f"/practice/questions/{self.question.pk}/figures/5/").status_code, 404)
        Question.objects.filter(pk=self.question.pk).update(status="draft")
        self.assertEqual(learner.get(url).status_code, 404)
        self.assertEqual(Client(HTTP_HOST="localhost").get(url).status_code, 302)

    def test_timed_snapshot_copies_images_and_only_owner_can_view(self):
        self.attach(png())
        test = TimedTest.objects.create(tenant=self.master, exam=self.exam, name="Figure test", duration_minutes=10, active=True,
            questions=[{"key": "q1", "question_id": self.question.pk, "stem": "Use the ray diagram", "subject": "Optics",
                        "question_type": "single_correct", "options": [{"id": "A", "text": "x"}, {"id": "B", "text": "y"}],
                        "answer": "A", "marking": {"correct": 4, "incorrect": -1, "unanswered": 0}}])
        learner = self.client_for(self.learner)
        learner.post(f"/practice/tests/{test.pk}/start/")
        attempt = test.timed_attempts.get()
        self.assertEqual(attempt.snapshot[0]["figures"], self.question.figure_paths)
        url = f"/practice/test-attempts/{attempt.pk}/figures/q1/0/"
        self.assertContains(learner.get(f"/practice/test-attempts/{attempt.pk}/"), url)
        self.assertEqual(learner.get(url).status_code, 200)
        self.assertEqual(self.client_for(self.other).get(url).status_code, 404)

    def zip_upload(self, rows, images):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("questions.xlsx", workbook_bytes("questions", rows))
            for name, raw in images.items():
                archive.writestr(f"images/{name}", raw)
            archive.writestr("__MACOSX/._junk.png", b"junk")
        return out.getvalue()

    def run_job(self, client, raw, name):
        client.post("/admin/imports/contentjob/add/", {"tenant": self.master.pk, "kind": "questions", "source": "Zip test",
            "license": "Test", "file": SimpleUploadedFile(name, raw)})
        job = ContentJob.objects.order_by("-pk").first()
        client.post(f"/admin/imports/contentjob/{job.pk}/review/", {"action": "process"})
        job.refresh_from_db()
        return job

    def row(self, **values):
        base = {"source_id": "zip-1", "kind": "PYQ", "exam": "fig-exam", "year": "2023", "subject": "Optics", "chapter": "Rays",
                "topic": "Mirrors", "question_type": "single_correct", "stem": "Zip diagram question", "option_a": "x",
                "option_b": "y", "answer": "A", "source": "Zip test", "license": "Test", "images": "ray.png, Lens.PNG"}
        return {**base, **values}

    def test_zip_import_attaches_images(self):
        client = self.client_for(self.root)
        job = self.run_job(client, self.zip_upload([self.row()], {"ray.png": png(), "lens.png": png((0, 120, 0))}), "bank.zip")
        self.assertEqual(job.status, "review", job.errors)
        self.assertFalse(Question.objects.filter(stem="Zip diagram question").exists())
        client.post(f"/admin/imports/contentjob/{job.pk}/review/", {"action": "commit", "reviewed": "yes"})
        job.refresh_from_db()
        self.assertEqual(job.status, "completed", job.errors)
        self.assertEqual(len(Question.objects.get(stem="Zip diagram question").figure_paths), 2)

    def test_zip_import_reports_missing_and_bad_images(self):
        client = self.client_for(self.root)
        job = self.run_job(client, self.zip_upload([self.row(images="ray.png, nope.png")], {"ray.png": b"not an image"}), "bad.zip")
        self.assertEqual(job.status, "failed")
        self.assertIn("nope.png", job.errors[0])
        job = self.run_job(client, self.zip_upload([self.row(images="ray.png")], {"ray.png": b"not an image"}), "bad2.zip")
        self.assertIn("not a readable image", job.errors[0])

    def test_images_column_without_zip_explains_what_to_do(self):
        client = self.client_for(self.root)
        job = self.run_job(client, workbook_bytes("questions", [self.row()]), "plain.xlsx")
        self.assertEqual(job.status, "failed")
        self.assertIn("Upload a .zip", job.errors[0])
