from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def material_upload_to(instance, filename):
    owner = instance.tenant_id or "global"
    return f"{owner}/study-material/{filename}"


class MaterialDocument(models.Model):
    class ProcessingStatus(models.TextChoices):
        NONE = "none", "Manual sections"
        QUEUED = "queued", "Queued for text extraction"
        COMPLETED = "completed", "Extracted"
        FAILED = "failed", "Failed"

    tenant = models.ForeignKey("tenancy.Tenant", null=True, blank=True, on_delete=models.CASCADE, related_name="material_documents")
    title = models.CharField(max_length=200)
    exams = models.ManyToManyField("catalog.Exam", blank=True, related_name="study_documents", help_text="Exams this material supports; empty means general reference material.")
    source = models.CharField(max_length=240)
    license = models.CharField(max_length=160)
    default_topic = models.ForeignKey("catalog.Topic", null=True, blank=True, on_delete=models.PROTECT)
    source_file = models.FileField(upload_to=material_upload_to, null=True, blank=True)
    processing_status = models.CharField(max_length=16, choices=ProcessingStatus.choices, default=ProcessingStatus.NONE)
    processing_error = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    active = models.BooleanField(default=True)
    vector_indexed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self): return self.title


class MaterialSection(models.Model):
    document = models.ForeignKey(MaterialDocument, on_delete=models.CASCADE, related_name="sections")
    title = models.CharField(max_length=200)
    body = models.TextField()
    topic = models.ForeignKey("catalog.Topic", on_delete=models.PROTECT, related_name="material_sections")
    concept_tags = models.ManyToManyField("content.ConceptTag", blank=True, related_name="material_sections")
    page_reference = models.CharField(max_length=80, blank=True)
    order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document__title", "order", "id"]
        indexes = [models.Index(fields=["topic", "active"])]

    def clean(self):
        if self.pk and self.concept_tags.exclude(topic_id=self.topic_id).exists():
            raise ValidationError({"concept_tags": "Concept tags must belong to this section's topic."})


class VideoMapping(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", null=True, blank=True, on_delete=models.CASCADE, related_name="video_mappings")
    concept_tag = models.ForeignKey("content.ConceptTag", on_delete=models.CASCADE, related_name="videos")
    title = models.CharField(max_length=200)
    url = models.URLField()
    start_seconds = models.PositiveIntegerField(default=0)
    source = models.CharField(max_length=160, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]


class QuestionMaterialLink(models.Model):
    question = models.ForeignKey('content.Question',on_delete=models.CASCADE,related_name='study_links')
    section = models.ForeignKey(MaterialSection,on_delete=models.CASCADE,related_name='question_links')
    note = models.CharField(max_length=240,blank=True)
    def clean(self):
        if self.question_id and self.section_id and self.question.tenant_id and self.section.document.tenant_id not in (None,self.question.tenant_id):
            raise ValidationError('Question and private material must belong to the same tenant.')

    class Meta:
        constraints=[models.UniqueConstraint(fields=['question','section'],name='unique_question_material_section')]
