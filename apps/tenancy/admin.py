from django.contrib import admin
from apps.audit.models import AuditEvent
from apps.features.models import FeatureEntitlement, TenantExam, TenantSubscription
from .models import Tenant


class FeatureEntitlementInline(admin.TabularInline):
    model = FeatureEntitlement
    fields = ("key", "entitled", "enabled_override")
    extra = 0


class TenantExamInline(admin.TabularInline):
    model = TenantExam
    fields = ("exam", "enabled")
    extra = 0


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_master", "status", "display_name", "created_at")
    inlines = (FeatureEntitlementInline, TenantExamInline)
    search_fields = ("name", "slug")
    list_filter = ("is_master", "status",)
    readonly_fields = ("is_master",)

    def get_readonly_fields(self, request, obj=None):
        return ("is_master", "slug") if obj and obj.is_master else self.readonly_fields

    def delete_queryset(self, request, queryset):
        queryset.filter(is_master=False).delete()

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        AuditEvent.objects.create(tenant=form.instance, actor=request.user,
            action="tenant_configuration_saved", object_type="Tenant", object_id=str(form.instance.pk),
            after={"name": form.instance.name, "is_master": form.instance.is_master, "status": form.instance.status})

    def has_module_permission(self, request): return request.user.is_superuser
    def has_view_permission(self, request, obj=None): return request.user.is_superuser
    def has_add_permission(self, request): return request.user.is_superuser
    def has_change_permission(self, request, obj=None): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser and not (obj and obj.is_master)


@admin.register(FeatureEntitlement)
class FeatureEntitlementAdmin(admin.ModelAdmin):
    list_display = ("tenant", "key", "entitled", "enabled_override", "updated_at")
    list_filter = ("key", "entitled", "enabled_override")
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def save_model(self, request, obj, form, change):
        before = {}
        if change:
            old = FeatureEntitlement.objects.get(pk=obj.pk)
            before = {"key": old.key, "entitled": old.entitled, "enabled_override": old.enabled_override}
        if not request.user.is_superuser:
            obj.tenant_id = getattr(request.user, "tenant_id", None)
            obj.entitled = FeatureEntitlement.objects.get(pk=obj.pk).entitled if obj.pk else False
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(tenant=obj.tenant, actor=request.user, action="tenant_feature_updated",
                                  object_type="FeatureEntitlement", object_id=str(obj.pk),
                                  before=before,
                                  after={"key": obj.key, "entitled": obj.entitled, "enabled_override": obj.enabled_override})
    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            fields += ["tenant", "key", "entitled"]
        return fields
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
    def has_module_permission(self, request):
        return request.user.is_superuser or getattr(request.user, "role", None) == "tenant_admin"
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or bool(getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) and
            (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None)))
    def has_add_permission(self, request): return request.user.is_superuser
    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser or bool(getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) and
            (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None)))


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tenant", "actor", "action", "object_type", "object_id")
    readonly_fields = ("tenant", "actor", "action", "object_type", "object_id", "before", "after", "created_at")
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or bool(getattr(request.user, "tenant_id", None) and
            (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None)))


@admin.register(TenantExam)
class TenantExamAdmin(admin.ModelAdmin):
    list_display = ("tenant", "exam", "enabled", "updated_at")
    list_filter = ("enabled", "exam")
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        from apps.catalog.models import Exam
        if not request.user.is_superuser and db_field.name == "tenant":
            kwargs["queryset"] = Tenant.objects.filter(pk=getattr(request.user, "tenant_id", None))
        if db_field.name == "exam": kwargs["queryset"] = Exam.objects.filter(active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser: obj.tenant_id = getattr(request.user, "tenant_id", None)
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(tenant=obj.tenant, actor=request.user, action="tenant_exam_updated",
            object_type="TenantExam", object_id=str(obj.pk), after={"exam": obj.exam.name, "enabled": obj.enabled})
    def has_module_permission(self, request):
        from .features import is_enabled
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "global_pyq_bank"))
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) and
            (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None)))
    def has_add_permission(self, request): return request.user.is_superuser or getattr(request.user, "role", None) == "tenant_admin"
    def has_change_permission(self, request, obj=None): return self.has_view_permission(request, obj)
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


@admin.register(TenantSubscription)
class TenantSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "plan", "status", "active_student_limit", "annual_price_inr", "trial_ends_at", "paid_through", "grace_ends_at")
    list_filter = ("plan", "status")
    readonly_fields = ("started_at", "updated_at")
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_module_permission(self, request): return request.user.is_superuser
    def has_add_permission(self, request): return request.user.is_superuser
    def has_change_permission(self, request, obj=None): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
    def save_model(self, request, obj, form, change):
        before = TenantSubscription.objects.filter(pk=obj.pk).values("plan", "status", "paid_through").first() if change else {}
        super().save_model(request, obj, form, change)
        if obj.status in (TenantSubscription.Status.TRIAL, TenantSubscription.Status.ACTIVE):
            if obj.tenant.status != "active":
                obj.tenant.status = "active"
                obj.tenant.save(update_fields=["status"])
        AuditEvent.objects.create(tenant=obj.tenant, actor=request.user, action="tenant_subscription_updated",
            object_type="TenantSubscription", object_id=str(obj.pk), before=before,
            after={"plan": obj.plan, "status": obj.status, "paid_through": obj.paid_through.isoformat() if obj.paid_through else None,
                   "active_student_limit": obj.active_student_limit, "annual_price_inr": obj.annual_price_inr})
