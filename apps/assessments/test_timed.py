"""Timed test exam flow: sections, key secrecy, tracking, proctoring and grading."""
import json

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.assessments.models import TimedAttempt, TimedTest
from apps.catalog.models import Exam
from apps.features.models import FeatureEntitlement, TenantExam
from apps.tenancy.models import Tenant

MARK = {"correct": 4, "incorrect": -1, "unanswered": 0}


def row(key, subject, kind="single_correct", answer="B"):
    options = [] if kind == "numerical" else [{"id": x, "text": f"Option {x}"} for x in "ABCD"]
    return {"key": key, "stem": f"Question {key} stem", "subject": subject, "question_type": kind,
            "options": options, "answer": answer, "marking": MARK, "solution": f"SECRET-SOLUTION-{key}"}


class TimedExamTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = Tenant.objects.get(is_master=True)
        FeatureEntitlement.objects.update_or_create(tenant=cls.master, key="tests", defaults={"entitled": True, "enabled_override": True})
        cls.exam = Exam.objects.create(slug="neet-test", name="NEET test", active=True)
        TenantExam.objects.create(tenant=cls.master, exam=cls.exam, enabled=True)
        cls.test = TimedTest.objects.create(tenant=cls.master, exam=cls.exam, name="Mixed paper", duration_minutes=30, active=True,
            questions=[row("p1", "Physics"), row("b1", "Biology"), row("b2", "Biology", "numerical", "2.5")])
        cls.user = User.objects.create_user(username="taker", email="taker@example.test", password="Taker-password-27",
                                            email_verified_at=timezone.now())

    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.client.force_login(self.user)

    def begin(self, test=None):
        test = test or self.test
        self.client.post(f"/practice/tests/{test.pk}/start/")
        return TimedAttempt.objects.get(test=test, user=self.user)

    def save(self, attempt, **data):
        return self.client.post(f"/practice/test-attempts/{attempt.pk}/", {"action": "save", **data})

    def test_instructions_show_sections_from_the_paper(self):
        page = self.client.get(f"/practice/tests/{self.test.pk}/start/")
        self.assertContains(page, "Biology")
        self.assertContains(page, "I have read the instructions")

    def test_exam_page_uses_paper_sections_and_hides_answer_keys(self):
        attempt = self.begin()
        page = self.client.get(f"/practice/test-attempts/{attempt.pk}/")
        self.assertContains(page, 'data-subject="Biology"')
        self.assertNotContains(page, 'data-subject="Mathematics"')
        self.assertNotContains(page, "SECRET-SOLUTION")
        self.assertNotContains(page, "vault-header")  # full-screen shell, no site navigation
        self.assertContains(page, 'id="exam-state"')

    def test_tracking_is_saved_and_validated(self):
        attempt = self.begin()
        response = self.save(attempt, answer_p1="B", review_b1="on", visited="p1,b1,unknown",
                             time_spent=json.dumps({"p1": 42, "b1": 99999, "nope": 5}),
                             events=json.dumps([{"type": "tab_hidden"}, {"type": "not-allowed"}]))
        self.assertEqual(response.json()["violations"], 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.answers, {"p1": ["B"]})
        self.assertEqual(attempt.review, ["b1"])
        self.assertEqual(attempt.visited, ["p1", "b1"])
        self.assertEqual(attempt.time_spent, {"p1": 42, "b1": 30 * 60})
        self.assertEqual([e["type"] for e in attempt.events], ["tab_hidden"])

    def test_events_are_capped(self):
        attempt = self.begin()
        self.save(attempt, events=json.dumps([{"type": "tab_hidden"}] * 200))
        attempt.refresh_from_db()
        self.assertEqual(len(attempt.events), 50)

    def test_without_limit_violations_are_only_logged(self):
        attempt = self.begin()
        for _ in range(5):
            self.save(attempt, events=json.dumps([{"type": "fullscreen_exit"}]))
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, len(attempt.events)), ("in_progress", 5))

    def test_limit_auto_submits_the_paper(self):
        strict = TimedTest.objects.create(tenant=self.master, exam=self.exam, name="Strict paper", duration_minutes=30,
            active=True, fullscreen_exit_limit=2, questions=[row("s1", "Physics")])
        attempt = self.begin(strict)
        self.assertNotIn("result_url", self.save(attempt, events=json.dumps([{"type": "fullscreen_exit"}])).json())
        payload = self.save(attempt, answer_s1="B", events=json.dumps([{"type": "tab_hidden"}])).json()
        self.assertEqual(payload["ended_by"], "proctoring")
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, attempt.result.get("ended_by"), float(attempt.score)), ("submitted", "proctoring", 4.0))

    def test_invalid_option_is_rejected(self):
        attempt = self.begin()
        self.assertEqual(self.save(attempt, answer_p1="Z").status_code, 403)

    def test_submit_grades_and_shows_result(self):
        attempt = self.begin()
        response = self.client.post(f"/practice/test-attempts/{attempt.pk}/",
                                    {"action": "submit", "answer_p1": "B", "answer_b1": "A", "answer_b2": "2.50"})
        self.assertRedirects(response, f"/practice/test-attempts/{attempt.pk}/result/", fetch_redirect_response=False)
        attempt.refresh_from_db()
        self.assertEqual(float(attempt.score), 7.0)  # +4 correct, -1 wrong, +4 numeric (2.50 == 2.5)
        self.assertContains(self.client.get(f"/practice/test-attempts/{attempt.pk}/result/"), "SECRET-SOLUTION-p1")
