from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "tenant", "role", "email_verified", "is_active", "is_staff")
    list_filter = ("role", "tenant", "tenant__is_master", "is_active", "is_staff")
    fieldsets = DjangoUserAdmin.fieldsets + (("PYQNet", {"fields": ("tenant", "role", "email_verified_at")}),)
    readonly_fields = ("email_verified_at",)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (("PYQNet", {"fields": ("email", "tenant", "role")}),)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser: return qs
        if getattr(request.user, "role", None) == User.Role.TENANT_ADMIN: return qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
        return qs.none()

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            obj.tenant_id = getattr(request.user, "tenant_id", None)
            if obj.is_superuser: obj.is_superuser = False
        super().save_model(request, obj, form, change)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser or getattr(request.user, "role", None) == User.Role.TENANT_ADMIN
