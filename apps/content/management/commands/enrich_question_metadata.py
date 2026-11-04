from django.core.management.base import BaseCommand
from django.db import transaction
from apps.content.models import Question
from apps.audit.models import AuditEvent


class Command(BaseCommand):
    help = 'Fill missing metadata provenance and synchronise question labels with the current taxonomy and difficulty.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        updated=0
        for item in Question.objects.select_related('paper__exam','topic__chapter__subject','subtopic'):
            if not item.topic_id:continue
            labels=[item.paper.exam.name if item.paper_id else 'Practice',item.topic.chapter.subject.name,item.topic.chapter.name,item.topic.name]
            if item.subtopic_id:labels.append(item.subtopic.name)
            if item.paper_id:labels.append(str(item.paper.year))
            labels.extend([item.get_question_type_display(),item.difficulty_label])
            tags=list(dict.fromkeys(labels))
            difficulty_basis=item.difficulty_basis or 'Editorial estimate for local practice; not student-calibrated.'
            taxonomy_basis=item.taxonomy_basis or 'Editorial classification of question content.'
            if tags!=item.tags or not item.difficulty_basis or not item.taxonomy_basis:
                item.tags=tags;item.difficulty_basis=difficulty_basis;item.taxonomy_basis=taxonomy_basis
                item.save(update_fields=['tags','difficulty_basis','taxonomy_basis'])
                updated+=1
        AuditEvent.objects.create(action='question_metadata_enriched',object_type='Question',object_id='metadata',after={'updated':updated})
        if options['dry_run']:transaction.set_rollback(True)
        self.stdout.write(f'{updated} questions updated.')
