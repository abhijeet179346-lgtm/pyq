from django.utils import timezone
"""Shared admin/worker operations for content jobs."""
from django.db import transaction
from apps.audit.models import AuditEvent
from .models import ContentJob
from .workbooks import open_upload,read_workbook
from .excel_services import apply_rows
from .pdf_excel import process_pdf_chunk


def process_job(job):
    if job.kind=='pdf':
        process_pdf_chunk(job)
        return
    claimed=ContentJob.objects.filter(pk=job.pk,status__in=['queued','failed','review']).update(status='processing',updated_at=timezone.now())
    if not claimed:
        job.refresh_from_db()
        return
    try:
        rows,images=load_job(job)
        count,errors=apply_rows(job,rows,preview=True,images=images)
        job.rows=rows;job.errors=errors;job.status='review' if not errors else 'failed'
    except Exception as exc:
        job.errors=[str(exc)[:1000]];job.status='failed'
    job.save(update_fields=['rows','errors','status','updated_at'])


def load_job(job):
    job.file.open('rb')
    try:workbook,images=open_upload(job.file.read(),job.file.name)
    finally:job.file.close()
    return read_workbook(workbook),images


@transaction.atomic
def commit_job(job_id,actor):
    job=ContentJob.objects.select_for_update().get(pk=job_id)
    if job.status!='review' or job.kind=='pdf':return job,0
    images=load_job(job)[1] if job.file.name.lower().endswith('.zip') else {}
    count,errors=apply_rows(job,job.rows,images=images)
    job.errors=errors;job.status='failed' if errors else 'completed'
    job.created_count=count if not errors else 0
    job.save(update_fields=['errors','status','created_count','updated_at'])
    AuditEvent.objects.create(tenant=job.tenant,actor=actor,action='excel_content_committed',object_type='ContentJob',object_id=str(job.pk),after={'created':job.created_count,'errors':errors[:20]})
    return job,count
