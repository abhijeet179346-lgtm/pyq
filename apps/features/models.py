from django.conf import settings
from django.db import models


class FeatureEntitlement(models.Model):
    """Plan-level allowlist plus tenant on/off override."""
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="feature_entitlements")
    key = models.SlugField(max_length=64)
    entitled = models.BooleanField(default=False)
    enabled_override = models.BooleanField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "key"], name="uniq_tenant_feature")]


class TenantExam(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="exam_settings")
    exam = models.ForeignKey("catalog.Exam", on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "exam"], name="uniq_tenant_exam_setting")]


class TenantSubscription(models.Model):
    class Plan(models.TextChoices):
        PILOT = "pilot", "Pilot"
        STARTER = "starter", "Starter"
        GROWTH = "growth", "Growth"
        ENTERPRISE = "enterprise", "Enterprise"

    class Status(models.TextChoices):
        TRIAL = "trial", "Trial"
        ACTIVE = "active", "Active"
        GRACE = "grace", "Grace period"
        SUSPENDED = "suspended", "Suspended"
        CANCELLED = "cancelled", "Cancelled"

    tenant = models.OneToOneField("tenancy.Tenant", on_delete=models.CASCADE, related_name="subscription")
    plan = models.CharField(max_length=16, choices=Plan.choices, default=Plan.PILOT)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TRIAL)
    active_student_limit = models.PositiveIntegerField(default=100)
    annual_price_inr = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    paid_through = models.DateField(null=True, blank=True)
    grace_ends_at = models.DateField(null=True, blank=True)
    billing_reference = models.CharField(max_length=160, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
