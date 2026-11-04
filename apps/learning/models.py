from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Batch(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="batches")
    name = models.CharField(max_length=140)
    exam = models.ForeignKey("catalog.Exam", null=True, blank=True, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["tenant", "name"], name="uniq_batch_name_per_tenant")]

    def __str__(self): return f"{self.name} ({self.tenant.slug})"


class BatchEnrollment(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="enrollments")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="batch_enrollments")
    active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "student"], name="uniq_student_batch_enrollment")]
        indexes = [models.Index(fields=["tenant", "batch", "active"])]

    def clean(self):
        if self.batch_id and self.tenant_id != self.batch.tenant_id:
            raise ValidationError("Enrollment and batch must belong to the same tenant.")
        if self.student_id and self.tenant_id != self.student.tenant_id:
            raise ValidationError("Enrollment and student must belong to the same tenant.")
        if self.student_id and self.student.role != "student":
            raise ValidationError("Only students may be enrolled in a batch.")


class TeacherSubjectAssignment(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subject_assignments")
    subject = models.ForeignKey("catalog.Subject", on_delete=models.CASCADE)
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["teacher", "subject", "batch"], name="uniq_teacher_subject_batch")]


class TeacherBatchAssignment(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teacher_batch_assignments")
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="teacher_assignments")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["teacher", "batch"], name="uniq_teacher_batch")]


class AccessCode(models.Model):
    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        TEACHER = "teacher", "Teacher"

    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="access_codes")
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="access_codes")
    digest = models.CharField(max_length=64, unique=True)
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.STUDENT)
    max_uses = models.PositiveIntegerField(default=1)
    used_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "batch", "revoked"])]

    def clean(self):
        if self.batch_id and self.tenant_id != self.batch.tenant_id:
            raise ValidationError("Access code and batch must belong to the same tenant.")
        if self.used_count > self.max_uses:
            raise ValidationError("used_count cannot exceed max_uses.")

    @property
    def exhausted(self): return self.used_count >= self.max_uses


class PracticeAttempt(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="practice_attempts")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.SET_NULL, related_name="practice_attempts")
    question = models.ForeignKey("content.Question", on_delete=models.PROTECT)
    question_version = models.PositiveIntegerField()
    selected_answer = models.TextField(blank=True)
    is_correct = models.BooleanField(default=False)
    time_taken_seconds = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "student", "submitted_at"])]
