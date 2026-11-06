"""Submit the source-linked bulk batch through Django's admin Content Job flow.

Run from the project root: python manage.py shell < scripts/import_bulk_pyqs.py
Completed rows are skipped on a repeated run. New rows remain drafts until review.
"""
import json
from collections import defaultdict
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
data = json.loads((root / 'data/bulk_pyq_batch.json').read_text())
groups = defaultdict(list)
for row in data['questions']:
    groups[(row['exam'], row['subject'])].append(row)

admin = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
tenant = Tenant.objects.filter(is_master=True).first()
if not admin or not tenant:
    raise RuntimeError('An active platform admin and master tenant are required.')
client = Client(HTTP_HOST='localhost')
client.force_login(admin)
output = root / 'data/admin_import_workbooks'
output.mkdir(exist_ok=True)

for (exam_slug, subject_name), rows in sorted(groups.items()):
    exam = Exam.objects.get(slug=exam_slug)
    subject = Subject.objects.get(name=subject_name)
    if not exam.subjects.filter(pk=subject.pk).exists():
        raise RuntimeError(f'{subject_name} is not enabled for {exam_slug}.')
    name = f'bulk-2024-{exam_slug}-{subject_name.lower().replace(" ", "-")}.xlsx'
    workbook = workbook_bytes('questions', rows)
    (output / name).write_bytes(workbook)
    pending = [row for row in rows if not Question.objects.filter(
        kind='PYQ', paper__exam=exam, section='Excel · ' + row['source_id'], source=row['source']).exists()]
    if not pending:
        print(f'{name}: {len(rows)} rows already imported')
        continue
    upload = SimpleUploadedFile(name, workbook_bytes('questions', pending),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response = client.post(reverse('admin:imports_contentjob_add'), {
        'tenant': tenant.pk, 'kind': 'questions', 'target_exam': exam.pk,
        'target_subject': subject.pk, 'file': upload, '_save': 'Save',
    })
    if response.status_code != 302:
        raise RuntimeError(f'{name}: admin add failed: {response.status_code} {response.content[:500]!r}')
    job = ContentJob.objects.order_by('-pk').first()
    if not job.file.name.endswith(name):
        raise RuntimeError(f'{name}: admin created unexpected file {job.file.name}')
    url = reverse('admin:contentjob_review', args=[job.pk])
    client.post(url, {'action': 'process'})
    job.refresh_from_db()
    if job.status != 'review' or job.errors:
        raise RuntimeError(f'{name}: validation failed: {job.errors[:10]}')
    client.post(url, {'action': 'commit', 'reviewed': 'yes'})
    job.refresh_from_db()
    if job.status != 'completed' or job.errors or job.created_count != len(pending):
        raise RuntimeError(f'{name}: commit failed: {job.status}, {job.created_count}/{len(pending)}, {job.errors[:10]}')
    print(f'{name}: job #{job.pk}, {job.created_count} draft questions imported')

print('Bulk import complete; review questions in admin before publishing.')
