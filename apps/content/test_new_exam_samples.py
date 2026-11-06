import io

from django.core.management import call_command
from django.test import TestCase

from apps.content.models import Question
from apps.learning.answering import is_correct


class NewExamSampleTests(TestCase):
    def test_sourced_samples_import_with_math_and_correct_answers(self):
        call_command('seed_exam_catalog', stdout=io.StringIO())
        call_command('import_new_exam_samples', dry_run=True, stdout=io.StringIO())
        self.assertEqual(Question.objects.count(), 0)
        call_command('import_new_exam_samples', stdout=io.StringIO())
        self.assertEqual(Question.objects.count(), 6)
        self.assertEqual(Question.objects.filter(paper__exam__slug='comedk').count(), 2)
        self.assertEqual(Question.objects.filter(paper__exam__slug='mht-cet').count(), 2)
        self.assertEqual(Question.objects.filter(paper__exam__slug='cuet-ug').count(), 2)
        for question in Question.objects.all():
            self.assertTrue(is_correct(question, [question.answer]))
            self.assertIn('$', question.stem + question.solution)
            question.full_clean()
