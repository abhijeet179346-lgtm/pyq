from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Question(models.Model):
    class Kind(models.TextChoices):
        PYQ = "PYQ", "Previous year question"
        PRACTICE = "PRACTICE", "Practice"

    class Type(models.TextChoices):
        SINGLE = "single_correct", "Single correct"
        MULTI = "multi_correct", "Multi correct"
        INTEGER = "integer", "Integer"
        NUMERICAL = "numerical", "Numerical"
        MATRIX = "matrix_match", "Matrix match"
        ASSERTION = "assertion_reason", "Assertion reason"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_REVIEW = "in_review", "In review"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired"

    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.PRACTICE)
    tenant = models.ForeignKey("tenancy.Tenant", null=True, blank=True, on_delete=models.CASCADE, related_name="questions")
    paper = models.ForeignKey("catalog.Paper", null=True, blank=True, on_delete=models.PROTECT, related_name="questions")
    section = models.CharField(max_length=80, blank=True)
    question_type = models.CharField(max_length=24, choices=Type.choices, default=Type.SINGLE)
    stem = models.TextField()
    options = models.JSONField(default=list, blank=True, help_text="List of option objects or strings")
    answer = models.TextField(help_text="Markdown/LaTeX answer")
    solution = models.TextField(blank=True)
    difficulty = models.PositiveSmallIntegerField(default=3, help_text="1–2 Easy, 3 Moderate, 4–5 Hard. See difficulty basis for provenance.")
    difficulty_basis = models.CharField(max_length=240, blank=True)
    taxonomy_basis = models.CharField(max_length=240, blank=True)
    tags = models.JSONField(default=list, blank=True, help_text="Searchable labels for exam, subject, chapter, topic, subtopic and skills.")
    topic = models.ForeignKey("catalog.Topic", null=True, blank=True, on_delete=models.PROTECT, related_name="questions")
    subtopic = models.ForeignKey("catalog.Subtopic", null=True, blank=True, on_delete=models.PROTECT, related_name="questions")
    source = models.CharField(max_length=240)
    license = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)
    ai_generated = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_questions")
    source_scan_id = models.PositiveBigIntegerField(null=True, blank=True)
    source_page = models.PositiveSmallIntegerField(null=True, blank=True)
    figure_paths = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["tenant", "status", "kind"]), models.Index(fields=["topic", "status"])]

    def clean(self):
        errors = {}
        if self.kind == self.Kind.PYQ and (not self.paper_id or self.tenant_id):
            errors["paper"] = "A PYQ must link to a global paper and have no tenant owner."
        if self.kind == self.Kind.PRACTICE and (not self.tenant_id or self.paper_id):
            errors["tenant"] = "Practice questions require a tenant and cannot link to a global paper."
        if self.subtopic_id and self.subtopic.topic_id != self.topic_id:
            errors["subtopic"] = "Subtopic must belong to the selected topic."
        if not isinstance(self.tags, list) or any(not isinstance(tag, str) for tag in self.tags):
            errors["tags"] = "Tags must be a list of text labels."
        if not 1 <= self.difficulty <= 5:
            errors["difficulty"] = "Difficulty must be between 1 and 5."
        if self.topic_id and self.topic.chapter.subject_id is None:
            errors["topic"] = "Topic must belong to a valid chapter and subject."
        if self.status == self.Status.PUBLISHED and not self.topic_id:
            errors["topic"] = "Published questions must be assigned to a topic."
        if errors:
            raise ValidationError(errors)

    @property
    def difficulty_label(self):
        return "Easy" if self.difficulty <= 2 else "Moderate" if self.difficulty == 3 else "Hard"

    def __str__(self): return f"{self.get_kind_display()}: {self.stem[:70]}"


class QuestionRevision(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="revisions")
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["question", "version"], name="uniq_question_revision")]


class ConceptTag(models.Model):
    topic = models.ForeignKey("catalog.Topic", on_delete=models.CASCADE, related_name="concept_tags")
    name = models.CharField(max_length=160)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["topic", "name"], name="uniq_concept_tag_topic_name")]
    def __str__(self): return self.name


class QuestionConceptTag(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="concept_links")
    concept_tag = models.ForeignKey(ConceptTag, on_delete=models.PROTECT)
    distractor_reason = models.TextField(blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["question", "concept_tag"], name="uniq_question_concept")]


class ContentReport(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="reports")
    tenant = models.ForeignKey("tenancy.Tenant", null=True, blank=True, on_delete=models.CASCADE)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["question", "reporter"], name="uniq_question_reporter")]
