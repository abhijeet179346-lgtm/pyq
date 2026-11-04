from django.conf import settings
from django.db import models


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        NEEDS_ATTENTION = "needs_attention", "Needs attention"
        READY = "ready", "Ready to commit"
        COMPLETED = "completed", "Completed"

    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="import_batches")
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    file_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices)
    row_count = models.PositiveIntegerField(default=0)
    valid_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ContentJob(models.Model):
    tenant = models.ForeignKey('tenancy.Tenant',on_delete=models.CASCADE)
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    kind = models.CharField(max_length=20,choices=[('questions','Excel questions'),('exams','Excel exam catalogue'),('tests','Excel timed tests'),('materials','Excel study sections'),('pdf','PDF to Excel')])
    file = models.FileField(upload_to='content-jobs/%Y/%m/')
    status = models.CharField(max_length=20,default='queued',choices=[('queued','Queued'),('processing','Processing'),('review','Review required'),('completed','Completed'),('failed','Failed')])
    source = models.CharField(max_length=240,blank=True)
    license = models.CharField(max_length=160,blank=True)
    use_ocr = models.BooleanField(default=False,help_text='Use configured vision provider for scanned pages; requires AI settings and budget.')
    created_count = models.PositiveIntegerField(default=0)
    next_page = models.PositiveIntegerField(default=0)
    page_count = models.PositiveIntegerField(default=0)
    rows = models.JSONField(default=list,blank=True)
    pages = models.JSONField(default=list,blank=True)
    errors = models.JSONField(default=list,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
