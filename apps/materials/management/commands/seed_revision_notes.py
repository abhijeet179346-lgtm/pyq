from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from apps.catalog.models import Subject, Chapter, Topic, Exam
from apps.content.models import ConceptTag
from apps.materials.models import MaterialDocument, MaterialSection
from apps.audit.models import AuditEvent


class Command(BaseCommand):
    help = 'Load original revision notes with formulas, examples and common mistakes; preserve editorial edits on reruns.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        source=Path(settings.BASE_DIR/'data/study_notes_source.txt').read_text()
        created=0
        for index, block in enumerate(source.split('@@')[1:], start=1):
            header,body=block.split('\n',1)
            subject_name,chapter_name,title=header.split('|')
            subject=Subject.objects.get(name=subject_name)
            chapter,_=Chapter.objects.get_or_create(subject=subject,name=chapter_name)
            topic=Topic.objects.filter(chapter=chapter).annotate(n=Count('questions')).order_by('-n','name').first()
            if topic is None:topic=Topic.objects.create(chapter=chapter,name='Revision essentials')
            doc,was_created=MaterialDocument.objects.get_or_create(tenant=None,source=f'pyq-vault:revision-notes:v1:{index}',defaults={
                'title':f'{subject_name} · {title}', 'license':'Original PYQ Vault revision notes; educational use.', 'default_topic':topic})
            doc.exams.set(Exam.objects.filter(slug__in=['neet'] if subject_name=='Biology' else ['jee-main','jee-advanced'] if subject_name=='Mathematics' else ['jee-main','jee-advanced','neet']))
            if was_created:
                section=MaterialSection.objects.create(document=doc,title=title,body=body.strip(),topic=topic,order=1)
                concept,_=ConceptTag.objects.get_or_create(topic=topic,name=topic.name)
                section.concept_tags.add(concept)
                created+=1
        AuditEvent.objects.create(action='revision_notes_seeded',object_type='MaterialDocument',object_id='revision-v1',after={'created':created})
        if options['dry_run']:transaction.set_rollback(True)
        self.stdout.write(f'{created} revision notes created. Existing notes preserved.')
