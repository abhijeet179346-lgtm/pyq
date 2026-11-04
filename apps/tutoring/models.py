from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class TutorThread(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ESCALATED = "escalated", "Escalated to teacher"
        CLOSED = "closed", "Closed"

    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="tutor_threads")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tutor_threads")
    topic = models.ForeignKey("catalog.Topic", null=True, blank=True, on_delete=models.SET_NULL)
    question = models.ForeignKey("content.Question", null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    subject_teacher = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="tutor_escalations")
    teaching_style = models.CharField(max_length=16, default="hint", choices=[("explain", "Explain"), ("hint", "Hint first"), ("simpler", "Simpler")])
    language = models.CharField(max_length=12, default="en", choices=[("en", "English"), ("hi", "Hindi"), ("hinglish", "Hinglish")])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class TutorMessage(models.Model):
    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        TUTOR = "tutor", "Tutor"
        TEACHER = "teacher", "Teacher"

    thread = models.ForeignKey(TutorThread, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=12, choices=Role.choices)
    content = models.TextField()
    citations = models.JSONField(default=list, blank=True)
    feedback = models.CharField(max_length=12, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class MistakeDiagnosis(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    practice_attempt = models.ForeignKey("learning.PracticeAttempt", null=True, blank=True, on_delete=models.CASCADE)
    test_answer = models.ForeignKey("assessments.AttemptAnswer", null=True, blank=True, on_delete=models.CASCADE)
    concept_tags = models.ManyToManyField("content.ConceptTag", blank=True)
    diagnosis = models.TextField(blank=True)
    explanation = models.TextField(blank=True)
    citations = models.JSONField(default=list, blank=True)
    rebuild_question = models.ForeignKey("content.Question", null=True, blank=True, on_delete=models.SET_NULL, related_name="rebuild_diagnoses")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["practice_attempt"], condition=models.Q(practice_attempt__isnull=False), name="uniq_practice_mistake_diagnosis"),
            models.UniqueConstraint(fields=["test_answer"], condition=models.Q(test_answer__isnull=False), name="uniq_test_mistake_diagnosis"),
        ]


class ConceptMastery(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="concept_mastery")
    concept_tag = models.ForeignKey("content.ConceptTag", on_delete=models.CASCADE)
    score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    evidence_count = models.PositiveIntegerField(default=0)
    last_evidence_at = models.DateTimeField(null=True, blank=True)
    next_review_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "student", "concept_tag"], name="uniq_tenant_student_concept_mastery")]
        ordering = ["score", "-evidence_count"]


class SpacedReview(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    question = models.ForeignKey("content.Question", on_delete=models.CASCADE)
    practice_attempt = models.ForeignKey("learning.PracticeAttempt", null=True, blank=True, on_delete=models.CASCADE)
    test_answer = models.ForeignKey("assessments.AttemptAnswer", null=True, blank=True, on_delete=models.CASCADE)
    due_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "student", "due_at", "completed_at"])]
        constraints = [
            models.UniqueConstraint(fields=["practice_attempt"], condition=models.Q(practice_attempt__isnull=False), name="uniq_spaced_review_practice_attempt"),
            models.UniqueConstraint(fields=["test_answer"], condition=models.Q(test_answer__isnull=False), name="uniq_spaced_review_test_answer"),
        ]


class TenantAISettings(models.Model):
    """Per-tenant model/cost policy. Provider keys remain in deployment secrets."""
    tenant = models.OneToOneField("tenancy.Tenant", on_delete=models.CASCADE, related_name="ai_settings")
    provider = models.CharField(max_length=32, default="openai_compatible")
    model = models.CharField(max_length=100, blank=True)
    vision_model = models.CharField(max_length=100, blank=True)
    monthly_budget_usd = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    daily_message_limit = models.PositiveSmallIntegerField(default=50)
    monthly_spent_usd = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    diagnosis_accuracy_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    answer_accuracy_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    citation_support_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    diagnosis_golden_set_size = models.PositiveIntegerField(default=0)
    answer_golden_set_size = models.PositiveIntegerField(default=0)
    quality_approved = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class AIUsage(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    operation = models.CharField(max_length=40)
    model = models.CharField(max_length=100, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=9, decimal_places=5, default=0)
    cache_key = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "created_at"])]


class TutorAnswerCache(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    cache_key = models.CharField(max_length=64)
    answer = models.TextField()
    citations = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "cache_key"], name="uniq_tenant_tutor_cache_key")]


class TutorGoldenCase(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="tutor_golden_cases")
    question = models.ForeignKey("content.Question", on_delete=models.CASCADE)
    student_answer = models.TextField()
    expected_concept = models.ForeignKey("content.ConceptTag", on_delete=models.PROTECT)
    predicted_concept = models.ForeignKey("content.ConceptTag", null=True, blank=True, on_delete=models.SET_NULL, related_name="predicted_golden_cases")
    generated_answer = models.TextField(blank=True)
    citations = models.JSONField(default=list, blank=True)
    answer_approved = models.BooleanField(null=True, blank=True)
    citations_supported = models.BooleanField(null=True, blank=True)
    evaluated_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "question", "student_answer", "expected_concept"], name="uniq_tenant_tutor_golden_case")]
        ordering = ["tenant", "evaluated_at", "id"]

    def clean(self):
        if self.question_id and self.question.tenant_id not in (None, self.tenant_id):
            raise ValidationError({"question": "Golden-case questions must be global or belong to this tenant."})
        if self.question_id and self.expected_concept_id and self.question.topic_id != self.expected_concept.topic_id:
            raise ValidationError({"expected_concept": "Expected concept must belong to the question's topic."})
