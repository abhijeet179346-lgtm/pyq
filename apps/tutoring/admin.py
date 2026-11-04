from django.contrib import admin

from apps.tenancy.features import is_enabled
from .models import (AIUsage, ConceptMastery, MistakeDiagnosis, TenantAISettings,
                     TutorAnswerCache, TutorGoldenCase, TutorMessage, TutorThread)


@admin.register(TenantAISettings)
class TenantAISettingsAdmin(admin.ModelAdmin):
    list_display = ("tenant", "provider", "model", "monthly_budget_usd", "monthly_spent_usd", "quality_approved")
    readonly_fields = ("monthly_spent_usd", "updated_at")
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) is not None)
    def has_add_permission(self, request): return request.user.is_superuser
    def has_change_permission(self, request, obj=None): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


@admin.register(AIUsage)
class AIUsageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tenant", "user", "operation", "model", "input_tokens", "output_tokens", "cost_usd")
    list_filter = ("operation", "tenant")
    readonly_fields = tuple(field.name for field in AIUsage._meta.fields)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
    def has_module_permission(self, request): return request.user.is_superuser or getattr(request.user, "role", None) == "tenant_admin"


@admin.register(TutorThread)
class TutorThreadAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "student", "topic", "status", "subject_teacher", "updated_at")
    list_filter = ("status", "topic__chapter__subject")
    readonly_fields = tuple(field.name for field in TutorThread._meta.fields)
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("student", "topic")
        if request.user.is_superuser: return qs
        if getattr(request.user, "role", None) == "teacher": return qs.filter(tenant_id=getattr(request.user, "tenant_id", None), subject_teacher=request.user)
        return qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "teacher_escalation"))
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


@admin.register(TutorMessage)
class TutorMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "thread", "role", "feedback")
    list_filter = ("role", "feedback")
    readonly_fields = tuple(field.name for field in TutorMessage._meta.fields)
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("thread")
        return qs if request.user.is_superuser else qs.filter(thread__tenant_id=getattr(request.user, "tenant_id", None))
    def has_module_permission(self, request): return request.user.is_superuser or getattr(request.user, "role", None) in ("tenant_admin", "teacher")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


@admin.register(MistakeDiagnosis)
class MistakeDiagnosisAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "student", "created_at", "rebuild_question")
    readonly_fields = tuple(field.name for field in MistakeDiagnosis._meta.fields)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
    def has_module_permission(self, request): return request.user.is_superuser or getattr(request.user, "role", None) == "tenant_admin"


@admin.register(ConceptMastery)
class ConceptMasteryAdmin(admin.ModelAdmin):
    list_display = ("tenant", "student", "concept_tag", "score", "evidence_count", "next_review_at")
    list_filter = ("tenant", "concept_tag__topic__chapter__subject")
    readonly_fields = tuple(field.name for field in ConceptMastery._meta.fields)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
    def has_module_permission(self, request): return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "mastery_analytics"))


@admin.register(TutorAnswerCache)
class TutorAnswerCacheAdmin(admin.ModelAdmin):
    list_display = ("tenant", "cache_key", "created_at")
    readonly_fields = tuple(field.name for field in TutorAnswerCache._meta.fields)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_module_permission(self, request): return request.user.is_superuser
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False


@admin.register(TutorGoldenCase)
class TutorGoldenCaseAdmin(admin.ModelAdmin):
    list_display = ("tenant", "question", "expected_concept", "predicted_concept", "answer_approved", "citations_supported", "evaluated_at")
    list_filter = ("tenant", "answer_approved", "citations_supported")
    search_fields = ("question__stem", "student_answer")
    readonly_fields = ("predicted_concept", "generated_answer", "citations", "evaluated_at", "reviewed_by")
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("question", "expected_concept")
        if request.user.is_superuser: return qs
        qs = qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
        if getattr(request.user, "role", None) == "teacher":
            from apps.learning.models import TeacherSubjectAssignment
            subjects = TeacherSubjectAssignment.objects.filter(teacher=request.user, tenant_id=getattr(request.user, "tenant_id", None)).values_list("subject_id", flat=True)
            qs = qs.filter(question__topic__chapter__subject_id__in=subjects)
        return qs
    def has_module_permission(self, request): return request.user.is_superuser or getattr(request.user, "role", None) in ("tenant_admin", "teacher")
    def has_add_permission(self, request): return request.user.is_superuser or getattr(request.user, "role", None) == "tenant_admin"
    def has_change_permission(self, request, obj=None): return request.user.is_superuser or getattr(request.user, "role", None) in ("tenant_admin", "teacher")
    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            obj.tenant_id = getattr(request.user, "tenant_id", None)
            obj.reviewed_by = request.user
        super().save_model(request, obj, form, change)
