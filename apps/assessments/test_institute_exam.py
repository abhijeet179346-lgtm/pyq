"""Institute (batch-assigned) tests on the shared exam screen."""
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.assessments.models import AttemptAnswer, TestAssignment, TestAttempt, TestQuestion, TestSection, TestTemplate
from apps.catalog.models import Chapter, Subject, Topic
from apps.content.models import Question
from apps.features.models import FeatureEntitlement
from apps.learning.models import Batch, BatchEnrollment
from apps.tenancy.models import Tenant


class InstituteExamTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Exam Institute", slug="examinst")
        for key in ("tests", "custom_test_assignment", "batch_management", "tenant_question_bank", "practice", "test_integrity"):
            FeatureEntitlement.objects.update_or_create(tenant=cls.tenant, key=key, defaults={"entitled": True, "enabled_override": True})
        topic = Topic.objects.create(chapter=Chapter.objects.create(subject=Subject.objects.create(name="Chemistry", slug="chem-i"), name="Atoms"), name="Shells")
        options = [{"id": "A", "text": "One"}, {"id": "B", "text": "Two"}]
        cls.q1 = Question.objects.create(tenant=cls.tenant, kind="PRACTICE", topic=topic, stem="Institute question one", options=options,
            answer="B", source="Fixture", license="Fixture", status="published")
        cls.q2 = Question.objects.create(tenant=cls.tenant, kind="PRACTICE", topic=topic, stem="Institute question two", options=options,
            answer="A", source="Fixture", license="Fixture", status="published")
        template = TestTemplate.objects.create(tenant=cls.tenant, name="Unit test", duration_minutes=30)
        cls.section = TestSection.objects.create(template=template, name="Section A", order=0, question_count=2,
            marking_scheme={"correct": 4, "incorrect": -1, "unanswered": 0})
        TestQuestion.objects.create(section=cls.section, question=cls.q1, order=0)
        TestQuestion.objects.create(section=cls.section, question=cls.q2, order=1)
        batch = Batch.objects.create(tenant=cls.tenant, name="Batch 1")
        cls.student = User.objects.create_user(username="inst-student", email="inst-student@example.test", password="Inst-student-27",
            tenant=cls.tenant, role="student", email_verified_at=timezone.now())
        BatchEnrollment.objects.create(tenant=cls.tenant, batch=batch, student=cls.student)
        now = timezone.now()
        cls.assignment = TestAssignment.objects.create(tenant=cls.tenant, template=template, batch=batch, title="Atoms unit test",
            available_from=now - timedelta(hours=1), deadline=now + timedelta(days=1))

    def setUp(self):
        self.client = Client(HTTP_HOST="examinst.localhost")
        self.client.force_login(self.student)

    def test_attempt_uses_exam_screen_and_saves_tracking(self):
        response = self.client.post(f"/tests/assignments/{self.assignment.pk}/start/")
        self.assertEqual(response.status_code, 302, response.content[:400])
        attempt = TestAttempt.objects.get(student=self.student)
        self.assertRedirects(response, f"/tests/attempts/{attempt.pk}/", fetch_redirect_response=False)
        page = self.client.get(f"/tests/attempts/{attempt.pk}/")
        self.assertContains(page, 'id="exam-state"')
        self.assertContains(page, 'data-subject="Chemistry"')
        self.assertContains(page, 'name="tab_switch_count"')
        self.assertNotContains(page, '"answer": "B"')
        saved = self.client.post(f"/tests/attempts/{attempt.pk}/", {"action": "save", f"answer_{self.q1.pk}": "B",
            f"review_{self.q2.pk}": "1", f"time_{self.q1.pk}": "42", "tab_switch_count": "2"})
        self.assertEqual(saved.json()["state"], "saved")
        answer = AttemptAnswer.objects.get(attempt=attempt, question=self.q1)
        self.assertEqual((answer.answer, answer.time_spent_seconds, answer.is_correct), ("B", 42, True))
        self.assertTrue(AttemptAnswer.objects.get(attempt=attempt, question=self.q2).marked_for_review)
        attempt.refresh_from_db()
        self.assertEqual(attempt.tab_switch_count, 2)
        # Reload restores selections and visited state for the palette.
        page = self.client.get(f"/tests/attempts/{attempt.pk}/")
        self.assertEqual(sorted(page.context["exam_state"]["visited"]), sorted([str(self.q1.pk), str(self.q2.pk)]))
        self.assertRegex(page.content.decode(), rf'name="answer_{self.q1.pk}" value="B" checked')
        submitted = self.client.post(f"/tests/attempts/{attempt.pk}/", {"action": "submit", f"answer_{self.q1.pk}": "B"})
        self.assertRedirects(submitted, f"/tests/attempts/{attempt.pk}/result/", fetch_redirect_response=False)
