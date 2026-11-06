"""Build the reviewed PYQ workbooks and submit them through admin Content Jobs.

Usage: python manage.py shell < scripts/import_reviewed_pyqs.py
The source manifest lives in data/reviewed_pyq_batch.json. Re-running is safe:
ContentJob import deduplicates on source, paper and source_id.
"""
import json
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
items = json.loads((root / 'data/reviewed_pyq_batch.json').read_text())
output = root / 'data/admin_import_workbooks'
output.mkdir(exist_ok=True)
admin = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
tenant = Tenant.objects.filter(is_master=True).first()
if not admin or not tenant:
    raise RuntimeError('An active platform admin and master tenant are required.')
client = Client(HTTP_HOST='localhost')
client.force_login(admin)

for (exam_slug, subject_name) in sorted({(item['exam'], item['subject']) for item in items}):
    exam = Exam.objects.get(slug=exam_slug)
    subject = Subject.objects.get(name=subject_name)
    if not exam.subjects.filter(pk=subject.pk).exists():
        raise RuntimeError(f'{subject_name} is not enabled for {exam_slug}.')
    selected = [item for item in items if item['exam'] == exam_slug and item['subject'] == subject_name]
    rows = []
    for item in selected:
        row = {key: str(value) for key, value in item.items() if key != 'options'}
        row['kind'] = 'PYQ'
        row['question_type'] = 'single_correct'
        for key, value in zip('abcd', item['options']):
            row[f'option_{key}'] = value
        rows.append(row)
    name = f'{exam_slug}-{subject_name.lower()}-reviewed-pyq.xlsx'
    data = workbook_bytes('questions', rows)
    (output / name).write_bytes(data)
    existing = ContentJob.objects.filter(kind='questions', file__endswith=name, status='completed').first()
    if existing:
        print(f'{name}: already imported as job #{existing.pk}')
        continue
    upload = SimpleUploadedFile(name, data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response = client.post(reverse('admin:imports_contentjob_add'), {
        'tenant': tenant.pk, 'kind': 'questions', 'target_exam': exam.pk,
        'target_subject': subject.pk, 'file': upload,
        'source': '', 'license': '',
        '_save': 'Save',
    })
    if response.status_code != 302:
        raise RuntimeError(f'{name}: admin upload failed ({response.status_code}): {response.content[:600]!r}')
    job = ContentJob.objects.order_by('-pk').first()
    if not job.file.name.endswith(name):
        raise RuntimeError(f'{name}: admin created an unexpected job file.')
    url = reverse('admin:contentjob_review', args=[job.pk])
    response = client.post(url, {'action': 'process'})
    job.refresh_from_db()
    if response.status_code != 302 or job.status != 'review' or job.errors:
        raise RuntimeError(f'{name}: validation failed: {job.errors}')
    response = client.post(url, {'action': 'commit', 'reviewed': 'yes'})
    job.refresh_from_db()
    if response.status_code != 302 or job.status != 'completed' or job.errors or job.created_count != len(rows):
        raise RuntimeError(f'{name}: commit failed: {job.status} {job.errors} {job.created_count}/{len(rows)}')
    print(f'{name}: job #{job.pk}, {job.created_count} draft questions imported')

print('New questions remain drafts for editorial review.')
