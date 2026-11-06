"""Create a source-linked MHT-CET workbook and import it as admin drafts.

Run from the project root: python manage.py shell < scripts/import_mht_cet_shift_pyqs.py
The source publisher's answer key needs subject review before publication.
"""
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.catalog.models import Exam, Subject
from apps.content.models import Question
from apps.imports.models import ContentJob
from apps.imports.workbooks import workbook_bytes
from apps.tenancy.models import Tenant


root = Path.cwd()
candidates = json.loads((root / 'data/mht_cet_pracbee_candidates.json').read_text())
exam = Exam.objects.get(slug='mht-cet')
actor = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
tenant = Tenant.objects.filter(is_master=True).first()
if not actor or not tenant:
    raise RuntimeError('An active platform admin and master tenant are required.')


def normalized(value):
    return re.sub(r'\W+', '', value).casefold()


this_batch_sections = {
    f'Excel · bulk-mht-cet-2024-{item["shift"]}-shift-{item["subject"].lower()}-q{item["number"]:03}'
    for item in candidates
}
existing = {subject: [normalized(stem) for stem in Question.objects.filter(
    kind='PYQ', paper__exam=exam, topic__chapter__subject__name=subject
).exclude(section__in=this_batch_sections).values_list('stem', flat=True)]
    for subject in ('Physics', 'Chemistry', 'Mathematics')}

rows = []
for item in candidates:
    subject = item['subject']
    stem = normalized(item['stem'])
    if len(stem) < 20:
        continue
    if any(stem == prior or (len(stem) > 55 and len(prior) > 55 and
           SequenceMatcher(None, stem, prior).ratio() > 0.92)
           for prior in existing[subject]):
        continue
    existing[subject].append(stem)
    shift = item['shift']
    source_id = f'bulk-mht-cet-2024-{shift}-shift-{subject.lower()}-q{item["number"]:03}'
    rows.append({
        'source_id': source_id, 'kind': 'PYQ', 'exam': 'mht-cet', 'year': '2024',
        'paper': shift.replace('-', ' ').title() + ' Shift', 'subject': subject,
        'chapter': '2024 Past Paper', 'topic': 'Source-based questions',
        'difficulty': '2', 'question_type': 'single_correct',
        'stem': item['stem'], 'option_a': item['options'][0],
        'option_b': item['options'][1], 'option_c': item['options'][2],
        'option_d': item['options'][3], 'answer': item['answer'],
        'solution': '', 'source': item['source'],
        'license': 'Source-linked past-paper transcription; original examination rights retained.',
        'review_status': 'Draft: publisher answer needs independent subject review',
        'original_text': item['stem'],
    })

output = root / 'data/admin_import_workbooks'
output.mkdir(exist_ok=True)
name = 'mht-cet-2024-pracbee-shift-papers-draft.xlsx'
workbook = workbook_bytes('questions', rows)
(output / name).write_bytes(workbook)
(root / 'data/mht_cet_shift_import.json').write_text(json.dumps(
    {'questions': rows, 'note': 'Source answer keys require subject review; imported as drafts.'},
    ensure_ascii=False, indent=2) + '\n')

pending = [row for row in rows if not Question.objects.filter(
    kind='PYQ', paper__exam=exam, section='Excel · ' + row['source_id'],
    source=row['source']).exists()]
if not pending:
    print(f'All {len(rows)} screened rows already imported.')
else:
    client = Client(HTTP_HOST='localhost')
    client.force_login(actor)
    # Admin destination is one subject per job; the workbook above remains a
    # single downloadable review artifact covering all three subjects.
    for subject_name in ('Physics', 'Chemistry', 'Mathematics'):
        subject_rows = [row for row in pending if row['subject'] == subject_name]
        if not subject_rows:
            continue
        subject = Subject.objects.get(name=subject_name)
        if not exam.subjects.filter(pk=subject.pk).exists():
            raise RuntimeError(f'{subject_name} is not enabled for MHT-CET.')
        upload_name = f'mht-cet-2024-shifts-{subject_name.lower()}-draft.xlsx'
        payload = workbook_bytes('questions', subject_rows)
        (output / upload_name).write_bytes(payload)
        upload = SimpleUploadedFile(upload_name, payload,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response = client.post(reverse('admin:imports_contentjob_add'), {
            'tenant': tenant.pk, 'kind': 'questions', 'target_exam': exam.pk,
            'target_subject': subject.pk, 'file': upload, '_save': 'Save',
        })
        if response.status_code != 302:
            raise RuntimeError(f'{subject_name} admin add failed: {response.status_code} {response.content[:500]!r}')
        job = ContentJob.objects.order_by('-pk').first()
        if not job.file.name.endswith(upload_name):
            raise RuntimeError(f'Unexpected uploaded file {job.file.name}')
        review = reverse('admin:contentjob_review', args=[job.pk])
        client.post(review, {'action': 'process'})
        job.refresh_from_db()
        if job.status != 'review' or job.errors:
            raise RuntimeError(f'{subject_name} validation failed: {job.status}, {job.errors[:10]}')
        client.post(review, {'action': 'commit', 'reviewed': 'yes'})
        job.refresh_from_db()
        if job.status != 'completed' or job.errors or job.created_count != len(subject_rows):
            raise RuntimeError(f'{subject_name} commit failed: {job.status}, {job.created_count}/{len(subject_rows)}, {job.errors[:10]}')
        print(f'{subject_name}: job #{job.pk}, {job.created_count} draft questions imported')

print(f'{len(rows)} screened source-linked candidates in {name}; publication needs subject review.')
