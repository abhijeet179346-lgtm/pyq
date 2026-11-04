from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        PLATFORM_ADMIN = "platform_admin", "Platform admin"
        TENANT_ADMIN = "tenant_admin", "Tenant admin"
        TEACHER = "teacher", "Teacher"
        STUDENT = "student", "Student"

    email = models.EmailField()
    tenant = models.ForeignKey("tenancy.Tenant", null=True, blank=True, on_delete=models.CASCADE, related_name="users")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)
    google_subject = models.CharField(max_length=255, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    core_account = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="institute_accounts")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "email"], condition=models.Q(tenant__isnull=False), name="uniq_user_email_per_tenant"),
            models.UniqueConstraint(fields=["email"], condition=models.Q(tenant__isnull=True), name="uniq_core_user_email"),
            models.UniqueConstraint(fields=["tenant", "google_subject"], condition=~models.Q(google_subject=""), name="uniq_google_subject_per_tenant"),
            models.UniqueConstraint(fields=["google_subject"], condition=models.Q(tenant__isnull=True) & ~models.Q(google_subject=""), name="uniq_core_google_subject"),
        ]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        if not self.is_superuser and self.tenant_id is None:
            from apps.tenancy.models import Tenant
            master = Tenant.objects.filter(is_master=True).first()
            if master:
                self.tenant = master
                if kwargs.get("update_fields") is not None:
                    kwargs["update_fields"] = set(kwargs["update_fields"]) | {"tenant"}
        if self.pk:
            original_email = type(self).objects.filter(pk=self.pk).values_list("email", flat=True).first()
            if original_email is not None and original_email != self.email:
                self.email_verified_at = None
                if kwargs.get("update_fields") is not None:
                    kwargs["update_fields"] = set(kwargs["update_fields"]) | {"email_verified_at"}
        super().save(*args, **kwargs)

    @property
    def email_verified(self):
        return self.email_verified_at is not None

    def clean(self):
        super().clean()
        if self.is_superuser and self.tenant_id is not None:
            raise ValidationError("Platform superusers must not belong to a tenant.")
        if not self.is_superuser and self.tenant_id is None and (self.role != self.Role.STUDENT or self.is_staff):
            raise ValidationError("An account without an institute must be a student without staff access.")


class LoginHandoff(models.Model):
    token_digest = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)


class EmailVerification(models.Model):
    token_digest = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_verifications")
    email = models.EmailField()
    password_fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
