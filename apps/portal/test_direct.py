import re

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Exam, Subject, Chapter, Topic, Paper
from apps.content.models import Question
from apps.features.models import TenantExam
from apps.materials.models import MaterialDocument, MaterialSection
from apps.portal.models import DirectPracticeAttempt
from apps.tenancy.models import Tenant


class DirectLearnerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="direct", email="direct@example.test",
            password="Direct-learner-password-27", email_verified_at=timezone.now())
        cls.other = User.objects.create_user(username="other-direct", email="other@example.test",
            password="Direct-learner-password-27", email_verified_at=timezone.now())
        cls.tenant = Tenant.objects.create(name="Private Institute", slug="private")
        exam = Exam.objects.create(name="Direct exam", slug="direct-exam")
        subject = Subject.objects.create(name="Physics", slug="physics")
        exam.subjects.add(subject)
        chapter = Chapter.objects.create(subject=subject, name="Mechanics")
        cls.topic = Topic.objects.create(chapter=chapter, name="Force")
        TenantExam.objects.create(tenant=cls.user.tenant, exam=exam, enabled=True)
        paper = Paper.objects.create(exam=exam, year=2025)
        common = {"topic": cls.topic, "options": [{"id": "A", "text": "First"}, {"id": "B", "text": "Second"}],
            "answer": "A", "source": "Original test fixture", "license": "Test fixture only"}
        cls.question = Question.objects.create(kind="PYQ", paper=paper, stem="Public practice question",
            solution="Explanation after answering", status="published", **common)
        cls.private = Question.objects.create(tenant=cls.tenant, kind="PRACTICE", stem="Private institute question",
            status="published", **common)
        Question.objects.create(kind="PYQ", paper=paper, stem="Unpublished draft question", status="draft", **common)
        global_doc = MaterialDocument.objects.create(title="Public notes", source="Fixture", license="Fixture")
        tenant_doc = MaterialDocument.objects.create(tenant=cls.tenant, title="Private notes", source="Fixture", license="Fixture")
        MaterialSection.objects.create(document=global_doc, title="Public lesson", body="Public body", topic=cls.topic)
        MaterialSection.objects.create(document=tenant_doc, title="Secret institute lesson", body="Private body", topic=cls.topic)

    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.client.force_login(self.user)

    def test_direct_account_opens_learning_workspace_without_tenant_or_code(self):
        response = self.client.get("/")
        self.assertContains(response, "Your personal learning workspace")
        self.assertNotContains(response, "Enter your access code")
        self.assertNotContains(response, "Manage institutes")
        self.assertNotContains(response, '/admin/')

    def test_removing_exam_subject_hides_existing_question(self):
        self.question.paper.exam.subjects.clear()
        response = self.client.get(f'/practice/?exam={self.question.paper.exam_id}&view=browse')
        self.assertNotContains(response, self.question.stem)
        self.assertEqual(self.client.get(f'/practice/questions/{self.question.pk}/').status_code, 404)
        self.assertTrue(self.user.tenant.is_master)

    def test_direct_bank_only_shows_published_global_pyqs(self):
        response = self.client.get(f"/practice/?exam={self.question.paper.exam_id}&view=browse")
        self.assertContains(response, "Public practice question")
        self.assertNotContains(response, "Private institute question")
        self.assertNotContains(response, "Unpublished draft question")
        self.assertEqual(self.client.get(f"/practice/questions/{self.private.pk}/").status_code, 404)

    def test_answering_saves_private_progress_and_retry_does_not_double_count(self):
        path = f"/practice/questions/{self.question.pk}/"
        page = self.client.get(path)
        self.assertNotContains(page, self.question.solution)
        token = re.search(r'name="submission" value="([^"]+)"', page.content.decode()).group(1)
        response = self.client.post(path, {"submission": token, "answer": "A"})
        self.assertContains(response, "Correct")
        self.assertContains(response, self.question.solution)
        self.client.post(path, {"submission": token, "answer": "B"})
        self.assertEqual(DirectPracticeAttempt.objects.filter(user=self.user).count(), 1)
        self.assertTrue(DirectPracticeAttempt.objects.get().is_correct)

    def test_submission_token_is_bound_to_signed_in_user(self):
        path = f"/practice/questions/{self.question.pk}/"
        page = self.client.get(path)
        token = re.search(r'name="submission" value="([^"]+)"', page.content.decode()).group(1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(path, {"submission": token, "answer": "A"}).status_code, 400)
        self.assertEqual(DirectPracticeAttempt.objects.count(), 0)

    def test_progress_is_not_visible_to_other_learners(self):
        path = f"/practice/questions/{self.question.pk}/"
        page = self.client.get(path)
        token = re.search(r'name="submission" value="([^"]+)"', page.content.decode()).group(1)
        self.client.post(path, {"submission": token, "answer": "B"})
        self.assertContains(self.client.get("/progress/"), self.question.stem)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get("/progress/"), self.question.stem)

    def test_direct_library_never_exposes_institute_material(self):
        response = self.client.get("/library/")
        self.assertContains(response, "Public lesson")
        self.assertNotContains(response, "Secret institute lesson")
        self.assertNotContains(response, "Private body")

    def test_direct_routes_do_not_run_on_institute_hosts(self):
        for path in ("/practice/", "/progress/", "/library/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, HTTP_HOST="private.localhost").status_code, 404)

    def test_unverified_direct_users_still_require_email_verification(self):
        self.user.email_verified_at = None
        self.user.save()
        for path in ("/", "/practice/", "/progress/", "/library/"):
            with self.subTest(path=path):
                self.assertRedirects(self.client.get(path), "/verify-email/", fetch_redirect_response=False)
