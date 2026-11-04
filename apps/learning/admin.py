from django.contrib import admin
from apps.tenancy.features import is_enabled
from apps.audit.models import AuditEvent
from apps.tenancy.features import is_enabled
from .models import AccessCode, Batch, BatchEnrollment, PracticeAttempt, TeacherBatchAssignment, TeacherSubjectAssignment


class TenantAdminMixin:
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser: return qs
        return qs.filter(tenant_id=getattr(request.user, "tenant_id", None)) if getattr(request.user, "tenant_id", None) else qs.none()

    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) is not None
            and is_enabled(request.user.tenant, "batch_management"))

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) is not None
               and is_enabled(request.user.tenant, "batch_management")
               and (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None)))

    def has_add_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) is not None)


@admin.register(Batch)
class BatchAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "tenant", "exam", "active", "student_count")
    list_filter = ("active", "exam")
    search_fields = ("name",)
    readonly_fields = ("tenant", "created_at")

    @admin.display(description="Students")
    def student_count(self, obj): return obj.enrollments.filter(active=True).count()

    def save_model(self, request, obj, form, change):
        before = {"name": obj.name, "active": obj.active} if change else {}
        if not request.user.is_superuser: obj.tenant_id = getattr(request.user, "tenant_id", None)
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(tenant=obj.tenant, actor=request.user, action="batch_saved", object_type="Batch",
                                  object_id=str(obj.pk), before=before, after={"name": obj.name, "active": obj.active})

    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "batch_management"))
    def has_add_permission(self, request): return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "batch_management"))
    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "batch_management") and (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None)))
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


@admin.register(AccessCode)
class AccessCodeAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("batch", "role", "max_uses", "used_count", "expires_at", "revoked", "created_at")
    list_filter = ("role", "revoked", "batch")
    readonly_fields = ("tenant", "batch", "digest", "role", "max_uses", "used_count", "expires_at", "created_by", "created_at")
    fields = readonly_fields + ("revoked",)
    ordering = ("-created_at",)
    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "access_code_enrollment"))
    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and
               getattr(request.user, "tenant_id", None) is not None and is_enabled(request.user.tenant, "access_code_enrollment") and
               (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None)))
    def has_change_permission(self, request, obj=None): return self.has_view_permission(request, obj)
    def save_model(self, request, obj, form, change):
        before = AccessCode.objects.get(pk=obj.pk).revoked if change else None
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(tenant=obj.tenant, actor=request.user, action="access_code_revoked" if obj.revoked else "access_code_updated",
                                  object_type="AccessCode", object_id=str(obj.pk), before={"revoked": before} if before is not None else {},
                                  after={"revoked": obj.revoked})


@admin.register(BatchEnrollment)
class BatchEnrollmentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("student", "batch", "active", "joined_at")
    list_filter = ("active", "batch")
    search_fields = ("student__email", "student__first_name", "student__last_name")
    readonly_fields = ("tenant", "batch", "student", "joined_at")
    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_view_permission(self, request, obj=None): return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "batch_management"))
    def has_change_permission(self, request, obj=None): return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "batch_management"))
    def save_model(self, request, obj, form, change):
        before = BatchEnrollment.objects.get(pk=obj.pk).active if change else None
        if not request.user.is_superuser: obj.tenant_id = getattr(request.user, "tenant_id", None)
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(tenant=obj.tenant, actor=request.user, action="enrollment_updated",
            object_type="BatchEnrollment", object_id=str(obj.pk),
            before={"active": before} if before is not None else {}, after={"active": obj.active})


@admin.register(TeacherBatchAssignment)
class TeacherBatchAssignmentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("teacher", "batch")
    readonly_fields = ("tenant", "teacher", "batch")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(TeacherSubjectAssignment)
class TeacherSubjectAssignmentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("teacher", "subject", "batch")
    readonly_fields = ("tenant", "teacher", "subject", "batch")
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(PracticeAttempt)
class PracticeAttemptAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("student", "batch", "question", "is_correct", "time_taken_seconds", "submitted_at")
    list_filter = ("is_correct", "batch")
    readonly_fields = tuple(field.name for field in PracticeAttempt._meta.fields)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
