from django.contrib import admin
from django import forms
from django.core.exceptions import PermissionDenied

from apps.learning.models import TeacherSubjectAssignment
from apps.tenancy.features import is_enabled
from .models import MaterialDocument, MaterialSection, VideoMapping, QuestionMaterialLink


class MaterialSectionInline(admin.TabularInline):
    model = MaterialSection
    extra = 0
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.name == "topic":
            from apps.catalog.models import Topic
            qs = Topic.objects.all()
            if getattr(request.user, "role", None) == "teacher":
                subjects = TeacherSubjectAssignment.objects.filter(tenant_id=getattr(request.user, "tenant_id", None),
                    teacher=request.user).values_list("subject_id", flat=True)
                qs = qs.filter(chapter__subject_id__in=subjects)
            kwargs["queryset"] = qs
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class MaterialDocumentForm(forms.ModelForm):
    class Meta:
        model = MaterialDocument
        fields = "__all__"
    def clean_source_file(self):
        file = self.cleaned_data.get("source_file")
        if not file: return file
        if file.size > 100 * 1024 * 1024:
            raise forms.ValidationError("Maximum material file size is 100 MB.")
        if not file.name.lower().endswith((".pdf", ".md", ".markdown", ".txt")):
            raise forms.ValidationError("Upload PDF, Markdown, or TXT. Scanned PDFs belong in the OCR import workflow.")
        return file


@admin.register(MaterialDocument)
class MaterialDocumentAdmin(admin.ModelAdmin):
    actions = ['extract_sections']
    @admin.action(description="Ingest text PDF/Markdown/TXT into study sections")
    def extract_sections(self,request,queryset):
        from .services import process_material_document
        for document in queryset:
            if not self.has_change_permission(request,document):raise PermissionDenied()
            try:
                count=process_material_document(document)
                self.message_user(request,f"{document.title}: {count} sections ingested. Review their topic assignments before using them for recommendations.")
            except Exception as exc:
                document.processing_status='failed';document.processing_error=str(exc)[:500];document.save(update_fields=['processing_status','processing_error'])
                self.message_user(request,f"{document.title}: {exc}",level='error')

    form = MaterialDocumentForm
    list_display = ("title", "tenant", "source", "license", "processing_status", "active", "created_at")
    search_fields = ("title", "source")
    list_filter = ("active", "tenant", "exams")
    filter_horizontal = ("exams",)
    inlines = [MaterialSectionInline]
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.name == "default_topic":
            from apps.catalog.models import Topic
            qs = Topic.objects.all()
            if getattr(request.user, "role", None) == "teacher":
                subjects = TeacherSubjectAssignment.objects.filter(tenant_id=getattr(request.user, "tenant_id", None),
                    teacher=request.user).values_list("subject_id", flat=True)
                qs = qs.filter(chapter__subject_id__in=subjects)
            kwargs["queryset"] = qs
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser: return qs
        return qs.filter(tenant_id=getattr(request.user, "tenant_id", None), uploaded_by=request.user) if getattr(request.user, "role", None) == "teacher" else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "material_import"))
    def has_add_permission(self, request): return self.has_module_permission(request)
    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request) and (obj is None or request.user.is_superuser or
            (obj.tenant_id == getattr(request.user, "tenant_id", None) and (getattr(request.user, "role", None) == "tenant_admin" or obj.uploaded_by_id == request.user.pk)))
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
    def save_model(self, request, obj, form, change):
        from apps.audit.models import AuditEvent
        if not request.user.is_superuser:
            obj.tenant_id = getattr(request.user, "tenant_id", None)
            if not change: obj.uploaded_by = request.user
        if obj.source_file and obj.processing_status == MaterialDocument.ProcessingStatus.NONE:
            obj.processing_status = MaterialDocument.ProcessingStatus.QUEUED
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(tenant=obj.tenant, actor=request.user, action="material_document_saved",
            object_type="MaterialDocument", object_id=str(obj.pk), after={"title": obj.title,
            "license": obj.license, "has_source_file": bool(obj.source_file)})


@admin.register(MaterialSection)
class MaterialSectionAdmin(admin.ModelAdmin):
    list_display = ("title", "document", "topic", "active", "order")
    list_filter = ("active", "topic__chapter__subject")
    search_fields = ("title", "body")
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("document")
        if request.user.is_superuser: return qs
        qs = qs.filter(document__tenant_id=getattr(request.user, "tenant_id", None))
        return qs.filter(document__uploaded_by=request.user) if getattr(request.user, "role", None) == "teacher" else qs
    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "material_import"))
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.name == "document":
            qs = MaterialDocument.objects.filter(tenant_id=getattr(request.user, "tenant_id", None))
            if getattr(request.user, "role", None) == "teacher": qs = qs.filter(uploaded_by=request.user)
            kwargs["queryset"] = qs
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if form.instance.concept_tags.exclude(topic_id=form.instance.topic_id).exists():
            from django.core.exceptions import ValidationError
            raise ValidationError("Concept tags must match this section's topic.")
        from apps.audit.models import AuditEvent
        AuditEvent.objects.create(tenant=form.instance.document.tenant, actor=request.user,
            action="material_section_saved", object_type="MaterialSection", object_id=str(form.instance.pk),
            after={"title": form.instance.title, "topic_id": form.instance.topic_id})


@admin.register(VideoMapping)
class VideoMappingAdmin(admin.ModelAdmin):
    list_display = ("title", "tenant", "concept_tag", "url", "active")
    list_filter = ("active", "tenant")
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "video_mapping"))
    def has_add_permission(self, request): return self.has_module_permission(request)
    def has_change_permission(self, request, obj=None): return self.has_module_permission(request)
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            obj.tenant_id = getattr(request.user, "tenant_id", None)
            obj.created_by = request.user
            if getattr(request.user, "role", None) == "teacher" and not TeacherSubjectAssignment.objects.filter(
                tenant=request.user.tenant, teacher=request.user, subject=obj.concept_tag.topic.chapter.subject).exists():
                raise PermissionDenied("You are not assigned to this subject.")
        super().save_model(request, obj, form, change)
