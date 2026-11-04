import io
import json
import re

from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.tenancy.models import Tenant
from apps.content.models import Question
from apps.learning.answering import is_correct
from apps.portal.models import DirectPracticeAttempt


class RealPYQSampleTests(TestCase):
    def test_dry_run_does_not_persist_sample(self):
        call_command("import_pyq_sample", dry_run=True, stdout=io.StringIO())
        self.assertEqual(Question.objects.count(), 0)

    def test_import_is_repeatable_with_three_exams_and_answer_types(self):
        for _ in range(2):
            call_command("import_pyq_sample", tenant=Tenant.objects.get(is_master=True).slug, stdout=io.StringIO())
        self.assertEqual(Question.objects.count(), 100)
        for exam, count in {"jee-main": 6, "jee-advanced": 88, "neet": 6}.items():
            self.assertEqual(Question.objects.filter(paper__exam__slug=exam).count(), count)
        self.assertSetEqual(set(Question.objects.values_list("question_type", flat=True)),
                            {"single_correct", "multi_correct", "integer"})
        for question in Question.objects.all():
            self.assertEqual(question.status, "published")
            self.assertIsNone(question.tenant_id)
            self.assertTrue(question.source.startswith("https://"))
            if question.source_page is not None:
                self.assertGreater(question.source_page, 0)
            self.assertTrue(question.concept_links.exists())
            question.full_clean()

    def test_each_imported_answer_scores_correctly_through_real_practice_view(self):
        call_command("import_pyq_sample", tenant=Tenant.objects.get(is_master=True).slug, stdout=io.StringIO())
        user = User.objects.create_user(username="real-pyq-learner", email="learner@example.test",
            password=None, email_verified_at=timezone.now())
        client = Client(HTTP_HOST="localhost")
        client.force_login(user)
        for question in Question.objects.all():
            with self.subTest(question=question.section, exam=question.paper.exam.slug):
                path = f"/practice/questions/{question.pk}/"
                page = client.get(path)
                self.assertEqual(page.status_code, 200)
                token = re.search(r'name="submission" value="([^"]+)"', page.content.decode()).group(1)
                answer = json.loads(question.answer) if question.question_type == "multi_correct" else question.answer
                response = client.post(path, {"submission": token, "answer": answer})
                self.assertEqual(response.status_code, 200)
                attempt = DirectPracticeAttempt.objects.get(user=user, question=question)
                self.assertTrue(attempt.is_correct)
                self.assertFalse(is_correct(question, ["definitely-incorrect-answer"]))
                if question.question_type == "multi_correct":
                    self.assertFalse(is_correct(question, [answer[0]]))
        self.assertEqual(DirectPracticeAttempt.objects.filter(user=user, is_correct=True).count(), 100)
