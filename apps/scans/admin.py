from django.contrib import admin
from apps.tenancy.features import is_enabled
from .models import ScanImport, TenantOCRSettings


@admin.register(ScanImport)
class ScanImportAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "file_name", "status", "extracted_count", "accepted_count", "created_at")
    list_filter = ("status", "tenant")
    readonly_fields = tuple(field.name for field in ScanImport._meta.fields)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_add_permission(self, request): return False
    def has_module_permission(self, request): return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "scan_import"))
    def has_change_permission(self, request, obj=None): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


@admin.register(TenantOCRSettings)
class TenantOCRSettingsAdmin(admin.ModelAdmin):
    list_display = ("tenant", "backend", "page_batch_size", "updated_at")
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            obj.tenant_id = request.user.tenant_id
        super().save_model(request, obj, form, change)
    def get_readonly_fields(self, request, obj=None):
        return () if request.user.is_superuser else ("tenant",)
    def has_module_permission(self, request):
        return request.user.is_superuser or getattr(request.user, "role", None) == "tenant_admin"
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and
            (obj is None or obj.tenant_id == request.user.tenant_id))
    has_change_permission = has_view_permission
    def has_add_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and
            request.user.tenant_id and not TenantOCRSettings.objects.filter(tenant_id=request.user.tenant_id).exists())
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
