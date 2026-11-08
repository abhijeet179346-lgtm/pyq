from django.contrib import admin
from django.core import mail
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone
from unittest.mock import patch

from apps.accounts.models import User
from apps.catalog.models import Chapter, Subject, Topic
from apps.content.models import Question
from apps.features.models import FeatureEntitlement
from apps.learning.models import Batch, BatchEnrollment
from apps.portal.models import StudentFeedback
from apps.tenancy.models import Tenant


@override_settings(REQUIRE_EMAIL_VERIFICATION=True,
                   FEEDBACK_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   FEEDBACK_NOTIFICATION_RECIPIENTS=["first@example.test", "second@example.test"],
                   FEEDBACK_FROM_EMAIL="sender@example.test")
class StudentFeedbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug="feedback-school", name="Feedback school")
        cls.other = Tenant.objects.create(slug="feedback-other", name="Other school")
        cls.student = User.objects.create_user(username="feedback-student", email="feedback@example.test",
            password="Example-password-123", tenant=cls.tenant, email_verified_at=timezone.now())
        cls.admin_user = User.objects.create_user(username="feedback-admin", email="admin-feedback@example.test",
            password="Example-password-123", tenant=cls.tenant, role="tenant_admin", is_staff=True,
            email_verified_at=timezone.now())
        batch = Batch.objects.create(tenant=cls.tenant, name="Feedback batch")
        BatchEnrollment.objects.create(tenant=cls.tenant, batch=batch, student=cls.student)
        for key in ("practice", "tenant_question_bank"):
            FeatureEntitlement.objects.create(tenant=cls.tenant, key=key, entitled=True)
        subject = Subject.objects.create(name="Feedback Physics", slug="feedback-physics")
        topic = Topic.objects.create(chapter=Chapter.objects.create(subject=subject, name="Motion"), name="Speed")
        cls.question = Question.objects.create(tenant=cls.tenant, kind="PRACTICE", topic=topic,
            stem="Student question", answer="A", source="Fixture", license="Fixture", status="published")
        cls.private = Question.objects.create(tenant=cls.other, kind="PRACTICE", topic=topic,
            stem="Other school question", answer="A", source="Fixture", license="Fixture", status="published")

    def setUp(self):
        self.client = Client(HTTP_HOST="feedback-school.localhost")
        self.client.force_login(self.student)

    def test_feedback_is_saved_with_tenant_user_and_question(self):
        response = self.client.post("/feedback/", {"category": "problem", "message": "The diagram is missing.",
            "question_id": self.question.pk, "page_path": "/practice/questions/123/?private=ignored"})
        self.assertEqual(response.status_code, 200)
        feedback = StudentFeedback.objects.get()
        self.assertEqual((feedback.tenant, feedback.reporter, feedback.question), (self.tenant, self.student, self.question))
        self.assertEqual(feedback.page_path, "/practice/questions/123/")
        self.assertIsNotNone(feedback.notification_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["first@example.test", "second@example.test"])
        self.assertEqual(mail.outbox[0].from_email, "sender@example.test")
        self.assertIn("The diagram is missing.", mail.outbox[0].body)
        self.assertIn(f"Question ID: {self.question.pk}", mail.outbox[0].body)
        self.assertContains(self.client.get(f"/practice/questions/{self.question.pk}/"), "This report will include the current question")

    def test_failed_notification_is_saved_for_retry(self):
        with patch("apps.portal.feedback_notifications.EmailMessage.send", side_effect=OSError("SMTP unavailable")):
            response = self.client.post("/feedback/", {"category": "feedback", "message": "Please improve the search."})
        self.assertEqual(response.status_code, 200)
        self.assertIn("email alert could not be sent", response.json()["message"])
        feedback = StudentFeedback.objects.get()
        self.assertIsNone(feedback.notification_sent_at)
        from apps.portal.feedback_notifications import notify_feedback
        self.assertTrue(notify_feedback(feedback))
        feedback.refresh_from_db()
        self.assertIsNotNone(feedback.notification_sent_at)

    def test_validation_and_tenant_isolation(self):
        self.assertEqual(self.client.post("/feedback/", {"category": "feedback", "message": "  "}).status_code, 400)
        self.assertEqual(self.client.post("/feedback/", {"category": "bad", "message": "Hi"}).status_code, 400)
        self.assertEqual(self.client.post("/feedback/", {"category": "problem", "message": "Hi", "question_id": self.private.pk}).status_code, 404)
        self.assertEqual(self.client.post("/feedback/", {"category": "problem", "message": "Hi"}, HTTP_HOST="feedback-other.localhost").status_code, 403)
        self.assertFalse(StudentFeedback.objects.exists())

    def test_tenant_admin_sees_only_own_feedback(self):
        own = StudentFeedback.objects.create(tenant=self.tenant, reporter=self.student, category="feedback", message="Own")
        StudentFeedback.objects.create(tenant=self.other, reporter=self.student, category="feedback", message="Other")
        request = RequestFactory().get("/admin/portal/studentfeedback/")
        request.user = self.admin_user
        model_admin = admin.site._registry[StudentFeedback]
        self.assertEqual(list(model_admin.get_queryset(request)), [own])
        self.assertTrue(model_admin.has_change_permission(request, own))
