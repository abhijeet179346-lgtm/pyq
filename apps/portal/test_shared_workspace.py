import re

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Chapter, Exam, Paper, Subject, Topic
from apps.content.models import Question
from apps.features.models import FeatureEntitlement, TenantExam
from apps.learning.models import Batch, BatchEnrollment, PracticeAttempt, TeacherBatchAssignment, TeacherSubjectAssignment
from apps.portal.models import DirectPracticeAttempt
from apps.tenancy.models import Tenant


@override_settings(REQUIRE_EMAIL_VERIFICATION=True)
class SharedTenantWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Shared institute", slug="shared")
        cls.other = Tenant.objects.create(name="Other institute", slug="other-shared")
        for key in ("practice", "tenant_question_bank", "mastery_analytics"):
            FeatureEntitlement.objects.create(tenant=cls.tenant, key=key, entitled=True)
        cls.student = User.objects.create_user(username="shared-student", email="student@shared.test",
            password="Example-password-123", tenant=cls.tenant, email_verified_at=timezone.now())
        cls.teacher = User.objects.create_user(username="shared-teacher", email="teacher@shared.test",
            password="Example-password-123", tenant=cls.tenant, role="teacher", email_verified_at=timezone.now())
        batch = Batch.objects.create(tenant=cls.tenant, name="Class")
        BatchEnrollment.objects.create(tenant=cls.tenant, batch=batch, student=cls.student)
        TeacherBatchAssignment.objects.create(tenant=cls.tenant, batch=batch, teacher=cls.teacher)
        subject = Subject.objects.create(name="Shared physics", slug="shared-physics")
        other_subject = Subject.objects.create(name="Shared chemistry", slug="shared-chemistry")
        TeacherSubjectAssignment.objects.create(tenant=cls.tenant, teacher=cls.teacher, subject=subject)
        topic = Topic.objects.create(chapter=Chapter.objects.create(subject=subject, name="Motion"), name="Speed")
        other_topic = Topic.objects.create(chapter=Chapter.objects.create(subject=other_subject, name="Atoms"), name="Elements")
        common = {"kind": "PRACTICE", "status": "published", "answer": "A",
                  "options": [{"id": "A", "text": "Answer"}], "source": "Fixture", "license": "Fixture"}
        cls.question = Question.objects.create(tenant=cls.tenant, topic=topic, stem="Institute physics question", **common)
        cls.other_subject_question = Question.objects.create(tenant=cls.tenant, topic=other_topic, stem="Institute chemistry question", **common)
        cls.private = Question.objects.create(tenant=cls.other, topic=topic, stem="Other institute question", **common)

    def setUp(self):
        self.client = Client(HTTP_HOST="shared.localhost")
        self.client.force_login(self.student)

    def test_tenant_uses_shared_dashboard_and_private_question_bank(self):
        response = self.client.get("/")
        self.assertContains(response, "Your personal learning workspace")
        self.assertContains(response, "Shared institute")
        response = self.client.get("/practice/?exam=practice&view=browse")
        self.assertContains(response, self.question.stem)
        self.assertNotContains(response, self.private.stem)
        self.assertEqual(self.client.get(f"/practice/questions/{self.private.pk}/").status_code, 404)

    def test_answer_is_recorded_once_for_institute_reports(self):
        path = f"/practice/questions/{self.question.pk}/"
        page = self.client.get(path)
        token = re.search(r'name="submission" value="([^"]+)"', page.content.decode()).group(1)
        self.client.post(path, {"submission": token, "answer": "A"})
        self.client.post(path, {"submission": token, "answer": "A"})
        self.assertEqual(DirectPracticeAttempt.objects.filter(user=self.student).count(), 1)
        attempt = PracticeAttempt.objects.get(student=self.student)
        self.assertEqual(attempt.batch.name, "Class")
        self.assertTrue(attempt.is_correct)
        self.assertContains(self.client.get("/"), "Institute physics question")

    def test_teacher_only_sees_assigned_subjects(self):
        self.client.force_login(self.teacher)
        response = self.client.get("/practice/?exam=practice&view=browse")
        self.assertContains(response, self.question.stem)
        self.assertNotContains(response, self.other_subject_question.stem)
        self.assertEqual(self.client.get(f"/practice/questions/{self.other_subject_question.pk}/").status_code, 404)

    def test_unenrolled_student_must_activate(self):
        BatchEnrollment.objects.filter(student=self.student).delete()
        self.assertRedirects(self.client.get("/practice/"), "/activate/", fetch_redirect_response=False)

    def test_tenant_exam_questions_are_in_the_exam_with_year_and_stay_private(self):
        exam = Exam.objects.create(name="Shared JEE Main", slug="shared-jee-main")
        exam.subjects.add(self.question.topic.chapter.subject)
        TenantExam.objects.create(tenant=self.tenant, exam=exam, enabled=True)
        FeatureEntitlement.objects.create(tenant=self.tenant, key="global_pyq_bank", entitled=True)
        self.question.exam = exam
        self.question.source_year = 2026
        self.question.options = [{"id": "A", "text": "First"}, {"id": "B", "text": "Second"}]
        self.question.save()
        self.private.exam = exam
        self.private.save()
        paper = Paper.objects.create(exam=exam, year=2025)
        Question.objects.create(kind="PYQ", paper=paper, topic=self.question.topic,
            stem="Global exam question", answer="A", status="published", source="Fixture", license="Fixture")

        response = self.client.get("/")
        self.assertContains(response, "Shared JEE Main")
        self.assertContains(response, "2 questions")
        response = self.client.get(f"/practice/?exam={exam.pk}&view=browse&year=2026")
        self.assertContains(response, self.question.stem)
        self.assertNotContains(response, "Global exam question")
        self.assertNotContains(response, self.private.stem)
        self.assertNotContains(self.client.get("/practice/?exam=practice&view=browse"), self.question.stem)
        self.assertContains(self.client.get(f"/practice/questions/{self.question.pk}/"), "Shared JEE Main 2026")

        from apps.assessments.custom_tests import build_custom_test
        test, skipped = build_custom_test(self.student, self.tenant, exam, [self.question], "Shared custom test")
        self.assertEqual(skipped, 0)
        self.assertEqual(test.questions[0]["question_id"], self.question.pk)
        TenantExam.objects.filter(tenant=self.tenant, exam=exam).update(enabled=False)
        self.assertEqual(self.client.get(f"/practice/questions/{self.question.pk}/").status_code, 404)

    def test_timed_tests_generate_from_the_selected_exam_and_subject(self):
        from apps.assessments.models import TimedTest
        exam = Exam.objects.create(name="Timed shared exam", slug="timed-shared-exam")
        exam.subjects.add(self.question.topic.chapter.subject, self.other_subject_question.topic.chapter.subject)
        TenantExam.objects.create(tenant=self.tenant, exam=exam, enabled=True)
        FeatureEntitlement.objects.create(tenant=self.tenant, key="tests", entitled=True)
        for question in (self.question, self.other_subject_question, self.private):
            question.exam = exam
            question.options = [{"id": "A", "text": "First"}, {"id": "B", "text": "Second"}]
            question.save()

        self.assertContains(self.client.get("/practice/tests/"), "Timed shared exam")
        self.assertContains(self.client.get(f"/practice/tests/?exam={exam.pk}"), "Generate subject test")
        response = self.client.post("/practice/tests/generate/", {"exam": exam.pk})
        full = TimedTest.objects.get(owner=self.student)
        self.assertRedirects(response, f"/practice/tests/{full.pk}/start/", fetch_redirect_response=False)
        self.assertEqual(full.duration_minutes, 180)
        self.assertEqual({row['question_id'] for row in full.questions}, {self.question.pk, self.other_subject_question.pk})
        self.client.post(f"/practice/tests/{full.pk}/start/")
        self.assertContains(self.client.get("/practice/tests/"), "Continue a test")

        self.client.post("/practice/tests/generate/", {"exam": exam.pk, "subject": "Shared physics"})
        subject_test = TimedTest.objects.filter(owner=self.student).exclude(pk=full.pk).get()
        self.assertEqual(subject_test.duration_minutes, 60)
        self.assertEqual([row['question_id'] for row in subject_test.questions], [self.question.pk])
        self.assertEqual(self.client.post("/practice/tests/generate/", {"exam": exam.pk, "subject": "Other institute"}).status_code, 404)
