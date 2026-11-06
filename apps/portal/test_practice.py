"""Practice sessions: setup, in-place checking, quiz secrecy, solutions, bookmarks, summary and retry."""
import re

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Chapter, Exam, Paper, Subject, Topic
from apps.content.models import Question
from apps.features.models import TenantExam
from apps.portal.models import DirectPracticeAttempt, PracticeBookmark, PracticeSession
from apps.tenancy.models import Tenant

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}


class PracticeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = Tenant.objects.get(is_master=True)
        cls.exam = Exam.objects.create(name="Practice exam", slug="practice-exam")
        TenantExam.objects.create(tenant=cls.master, exam=cls.exam, enabled=True)
        paper = Paper.objects.create(exam=cls.exam, year=2024)
        subject = Subject.objects.create(name="Physics", slug="physics-p")
        cls.exam.subjects.add(subject)
        cls.topic = Topic.objects.create(chapter=Chapter.objects.create(subject=subject, name="Mechanics"), name="Force")
        options = [{"id": "A", "text": "First"}, {"id": "B", "text": "Second"}]
        cls.questions = [Question.objects.create(kind="PYQ", paper=paper, topic=cls.topic, stem=f"Practice stem {i}", options=options,
            answer="A", solution=f"SOLUTION-{i}", source="Fixture", license="Fixture", status="published") for i in range(3)]
        cls.user = User.objects.create_user(username="learner-p", email="learner-p@example.test", password="Practice-password-27",
                                            email_verified_at=timezone.now())
        cls.other = User.objects.create_user(username="other-p", email="other-p@example.test", password="Practice-password-27",
                                             email_verified_at=timezone.now())

    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.client.force_login(self.user)

    def start(self, **extra):
        response = self.client.post("/practice/start/", {"exam": self.exam.pk, "action": "practice", **extra})
        session = PracticeSession.objects.filter(user=self.user).first()
        return response, session

    def answer(self, session, position, value, **headers):
        url = f"/practice/sessions/{session.pk}/{position}/"
        token = re.search(r'name="submission" value="([^"]+)"', self.client.get(url).content.decode()).group(1)
        return self.client.post(url, {"submission": token, "answer": value}, **headers)

    def test_start_creates_saved_session_with_count_and_mode(self):
        response, session = self.start(count="2", mode="quiz", order="sequential")
        self.assertRedirects(response, f"/practice/sessions/{session.pk}/1/", fetch_redirect_response=False)
        self.assertEqual((len(session.question_ids), session.mode), (2, "quiz"))
        page = self.client.get(f"/practice/sessions/{session.pk}/1/")
        self.assertContains(page, "Question 1 of 2")
        self.assertNotContains(page, "vault-header")  # focused shell

    def test_learn_mode_checks_in_place(self):
        _, session = self.start(mode="learn")
        data = self.answer(session, 1, "B", **FETCH).json()
        self.assertEqual((data["state"], data["answer_ids"], data["solution"]), ("wrong", ["A"], "SOLUTION-0"))
        self.assertEqual(data["palette"][0]["state"], "wrong")
        self.assertEqual((data["answered"], data["correct"]), (1, 0))
        data = self.answer(session, 2, "A", **FETCH).json()
        self.assertEqual((data["state"], data["correct"]), ("correct", 1))
        session.refresh_from_db()
        self.assertEqual(sorted(session.answers), ["1", "2"])
        # Reloading an answered question shows feedback, and re-posting does not add attempts.
        page = self.client.get(f"/practice/sessions/{session.pk}/1/")
        self.assertContains(page, "✗ Your answer")
        self.assertContains(page, "✓ Correct answer")
        self.assertEqual(DirectPracticeAttempt.objects.filter(user=self.user).count(), 2)

    def test_quiz_mode_never_reveals_answers_before_summary(self):
        _, session = self.start(mode="quiz")
        data = self.answer(session, 1, "B", **FETCH).json()
        self.assertEqual(data["state"], "saved")
        self.assertEqual((data["answer_ids"], data["solution"]), ([], ""))
        self.assertNotIn("correct", data)
        page = self.client.get(f"/practice/sessions/{session.pk}/1/")
        self.assertNotContains(page, "SOLUTION-0")
        self.assertNotContains(page, "Correct answer")
        url = f"/practice/sessions/{session.pk}/2/"
        self.assertEqual(self.client.post(url, {"action": "reveal"}, **FETCH).status_code, 403)
        summary = self.client.get(f"/practice/sessions/{session.pk}/")
        self.assertContains(summary, "SOLUTION-0")

    def test_show_solution_before_answering(self):
        _, session = self.start()
        url = f"/practice/sessions/{session.pk}/1/"
        data = self.client.post(url, {"action": "reveal"}, **FETCH).json()
        self.assertEqual((data["state"], data["solution"]), ("revealed", "SOLUTION-0"))
        session.refresh_from_db()
        self.assertEqual(session.revealed, [1])
        self.assertContains(self.client.get(url), "Solution viewed")
        self.assertFalse(DirectPracticeAttempt.objects.filter(user=self.user).exists())

    def test_bookmarks_toggle_list_and_practise(self):
        question = self.questions[1]
        url = f"/practice/questions/{question.pk}/bookmark/"
        self.assertTrue(self.client.post(url, **FETCH).json()["bookmarked"])
        self.assertContains(self.client.get("/progress/"), "Practice stem 1")
        response = self.client.post("/practice/bookmarks/start/")
        session = PracticeSession.objects.filter(user=self.user).first()
        self.assertEqual((session.title, session.question_ids), ("Bookmarked questions", [question.pk]))
        self.assertRedirects(response, f"/practice/sessions/{session.pk}/1/", fetch_redirect_response=False)
        self.assertFalse(self.client.post(url, **FETCH).json()["bookmarked"])
        self.assertFalse(PracticeBookmark.objects.exists())

    def test_summary_and_retry_wrong(self):
        _, session = self.start()
        self.answer(session, 1, "B", **FETCH)
        self.answer(session, 2, "A", **FETCH)
        summary = self.client.get(f"/practice/sessions/{session.pk}/")
        self.assertEqual((summary.context["correct"], summary.context["wrong"], summary.context["skipped"]), (1, 1, 1))
        self.assertEqual(summary.context["chapters"][0]["accuracy"], 50)
        self.client.post(f"/practice/sessions/{session.pk}/retry/")
        retry = PracticeSession.objects.filter(user=self.user).first()
        self.assertEqual(retry.question_ids, [self.questions[0].pk])
        self.client.post(f"/practice/sessions/{session.pk}/retry/", {"include": "skipped"})
        retry = PracticeSession.objects.filter(user=self.user).first()
        self.assertEqual(retry.question_ids, [self.questions[0].pk, self.questions[2].pk])

    def test_sessions_are_private(self):
        _, session = self.start()
        other = Client(HTTP_HOST="localhost")
        other.force_login(self.other)
        self.assertEqual(other.get(f"/practice/sessions/{session.pk}/1/").status_code, 404)
        self.assertEqual(other.get(f"/practice/sessions/{session.pk}/").status_code, 404)

    def test_check_without_javascript_still_works(self):
        _, session = self.start()
        page = self.answer(session, 1, "A")
        self.assertContains(page, "✓ Correct")
        self.assertContains(page, "SOLUTION-0")


class CustomTestBuilderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.features.models import FeatureEntitlement
        cls.master = Tenant.objects.get(is_master=True)
        FeatureEntitlement.objects.update_or_create(tenant=cls.master, key="tests", defaults={"entitled": True, "enabled_override": True})
        cls.exam = Exam.objects.create(name="JEE Advanced", slug="jee-advanced", active=True)
        TenantExam.objects.create(tenant=cls.master, exam=cls.exam, enabled=True)
        paper = Paper.objects.create(exam=cls.exam, year=2023)
        topic = Topic.objects.create(chapter=Chapter.objects.create(subject=Subject.objects.create(name="Maths", slug="maths-c"), name="Algebra"), name="Roots")
        cls.exam.subjects.add(topic.chapter.subject)
        options = [{"id": "A", "text": "2"}, {"id": "B", "text": "3"}]
        common = dict(kind="PYQ", paper=paper, topic=topic, source="Fixture", license="Fixture", status="published")
        cls.single = Question.objects.create(stem="Custom single", options=options, answer="B", **common)
        cls.numeric = Question.objects.create(stem="Custom numeric", options=[], answer="7.5", question_type="numerical", **common)
        cls.bad = Question.objects.create(stem="Custom unmarkable", options=[], answer="about seven", question_type="numerical", **common)
        cls.user = User.objects.create_user(username="builder", email="builder@example.test", password="Builder-password-27",
                                            email_verified_at=timezone.now())
        cls.other = User.objects.create_user(username="nosy", email="nosy@example.test", password="Builder-password-27",
                                             email_verified_at=timezone.now())

    def client_for(self, user):
        client = Client(HTTP_HOST="localhost")
        client.force_login(user)
        return client

    def test_builds_private_test_with_exam_pattern_and_grades_it(self):
        from apps.assessments.models import TimedAttempt, TimedTest
        client = self.client_for(self.user)
        response = client.post("/practice/start/", {"exam": self.exam.pk, "action": "test", "count": "all"})
        test = TimedTest.objects.get(owner=self.user)
        self.assertRedirects(response, f"/practice/tests/{test.pk}/start/", fetch_redirect_response=False)
        self.assertEqual(len(test.questions), 2)  # the unmarkable numeric was skipped
        self.assertEqual(test.duration_minutes, 10)  # 2 questions × 3.3 min, rounded up to 5-minute steps
        rows = {row["key"]: row for row in test.questions}
        self.assertEqual((rows[f"q{self.single.pk}"]["answer"], rows[f"q{self.single.pk}"]["marking"]["correct"]), ("B", 3))
        self.assertContains(client.get("/practice/tests/"), "Your custom tests")
        self.assertNotContains(self.client_for(self.other).get("/practice/tests/"), "Your custom tests")
        self.assertEqual(self.client_for(self.other).get(f"/practice/tests/{test.pk}/start/").status_code, 404)
        client.post(f"/practice/tests/{test.pk}/start/")
        attempt = TimedAttempt.objects.get(test=test, user=self.user)
        client.post(f"/practice/test-attempts/{attempt.pk}/", {"action": "submit", f"answer_q{self.single.pk}": "B", f"answer_q{self.numeric.pk}": "7.50"})
        attempt.refresh_from_db()
        self.assertEqual(float(attempt.score), 6.0)

    def test_practice_only_bank_cannot_become_a_test(self):
        client = self.client_for(self.user)
        response = client.post("/practice/start/", {"exam": "practice", "action": "test"})
        from apps.assessments.models import TimedTest
        self.assertFalse(TimedTest.objects.filter(owner=self.user).exists())
        self.assertIn(response.status_code, (302, 404))
