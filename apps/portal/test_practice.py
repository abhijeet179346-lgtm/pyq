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
