"""Repair obvious encoding damage and undelimited LaTeX tables in stored questions."""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.content.math_text import repair_question_fields
from apps.content.models import Question, QuestionRevision


class Command(BaseCommand):
    help = 'Preview or apply conservative math text repairs to existing questions.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Save repairs and question revisions.')

    def handle(self, *args, **options):
        changed = 0
        with transaction.atomic():
            for question in Question.objects.iterator():
                updates = repair_question_fields(question)
                if not updates:
                    continue
                changed += 1
                if options['apply']:
                    QuestionRevision.objects.get_or_create(question=question, version=question.version,
                        defaults={'snapshot': {field: getattr(question, field) for field in updates} |
                            {'version': question.version}})
                    for field, value in updates.items():
                        setattr(question, field, value)
                    question.version += 1
                    question.save(update_fields=[*updates, 'version', 'updated_at'])
        self.stdout.write(f'{changed} question(s) {"repaired" if options["apply"] else "would be repaired"}.')
