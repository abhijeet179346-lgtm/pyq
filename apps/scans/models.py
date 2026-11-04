from django.conf import settings
from django.db import models


def scan_upload_to(instance, filename):
    return f"tenant-{instance.tenant_id}/scan-imports/{instance.pk or 'new'}/{filename}"


class ScanImport(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        REVIEW = "review", "Needs review"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="scan_imports")
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    file = models.FileField(upload_to=scan_upload_to)
    file_name = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    page_count = models.PositiveIntegerField(default=0)
    next_page = models.PositiveIntegerField(default=0, help_text="Number of pages successfully processed.")
    extracted_count = models.PositiveIntegerField(default=0)
    accepted_count = models.PositiveIntegerField(default=0)
    accepted_rows = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    extraction_payload = models.JSONField(default=list, blank=True)
    page_image_paths = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=240, blank=True)
    license = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class TenantOCRSettings(models.Model):
    class Backend(models.TextChoices):
        VISION = "vision", "Vision model"
        DOCLING_LOCAL = "docling_local", "Docling local OCR and question parsing"
        DOCLING_VISION = "docling_vision", "Docling layout and OCR + vision model"

    tenant = models.OneToOneField("tenancy.Tenant", on_delete=models.CASCADE, related_name="ocr_settings")
    backend = models.CharField(max_length=24, choices=Backend.choices, default=Backend.VISION)
    page_batch_size = models.PositiveSmallIntegerField(default=10)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if not 1 <= self.page_batch_size <= 25:
            raise ValidationError({"page_batch_size": "Use 1 to 25 pages per batch."})
