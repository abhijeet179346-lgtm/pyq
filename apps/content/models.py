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
    exam = models.ForeignKey("catalog.Exam", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="tenant_questions", help_text="Exam for a tenant-owned question, if known.")
    source_year = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Original exam year for a tenant-owned question, if known.")
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
    fingerprint = models.CharField(max_length=64, blank=True, db_index=True, editable=False,
                                   help_text="Hash of the normalised stem and options, used to block duplicates.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["tenant", "status", "kind"]), models.Index(fields=["topic", "status"])]
        constraints = [
            # One copy of a question per global paper, and per institute bank. Retired copies don't count.
            models.UniqueConstraint(fields=["paper", "fingerprint"], name="uniq_question_per_paper",
                condition=models.Q(paper__isnull=False) & ~models.Q(fingerprint="") & ~models.Q(status="retired")),
            models.UniqueConstraint(fields=["tenant", "fingerprint"], name="uniq_question_per_tenant",
                condition=models.Q(tenant__isnull=False) & ~models.Q(fingerprint="") & ~models.Q(status="retired")),
        ]

    def refresh_fingerprint(self):
        from .fingerprint import question_fingerprint
        self.fingerprint = question_fingerprint(self.stem, self.options, self.figure_paths)
        return self.fingerprint

    def duplicate(self):
        """An existing live question this one would duplicate (same paper, or same institute bank)."""
        if not self.refresh_fingerprint() or self.status == self.Status.RETIRED:
            return None
        if self.paper_id:
            scope = models.Q(paper_id=self.paper_id)
        elif self.tenant_id:
            scope = models.Q(tenant_id=self.tenant_id)
        else:
            return None
        return (Question.objects.filter(scope, fingerprint=self.fingerprint).exclude(status=self.Status.RETIRED)
                .exclude(pk=self.pk).order_by("pk").first())

    def save(self, *args, **kwargs):
        self.refresh_fingerprint()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and {"stem", "options"} & set(update_fields):
            kwargs["update_fields"] = set(update_fields) | {"fingerprint"}
        super().save(*args, **kwargs)

    def clean(self):
        errors = {}
        original = self.duplicate()
        if original:
            errors["stem"] = (f"This question already exists as #{original.pk} "
                              f"({'same paper' if self.paper_id else 'same institute bank'}). Edit that one instead, "
                              "or retire it first.")
        if self.kind == self.Kind.PYQ and (not self.paper_id or self.tenant_id):
            errors["paper"] = "A PYQ must link to a global paper and have no tenant owner."
        if self.kind == self.Kind.PYQ and (self.exam_id or self.source_year):
            errors["exam"] = "Global PYQs take their exam and year from their paper."
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
        if self.kind == self.Kind.PYQ and self.paper_id and self.topic_id:
            exam = self.paper.exam
            if not exam.subjects.filter(pk=self.topic.chapter.subject_id).exists():
                errors["topic"] = f"{self.topic.chapter.subject} is not enabled for {exam}. Edit the exam's subjects in admin."
        if self.status == self.Status.PUBLISHED and not self.topic_id:
            errors["topic"] = "Published questions must be assigned to a topic."
        if errors:
            raise ValidationError(errors)

    @property
    def difficulty_label(self):
        return "Easy" if self.difficulty <= 2 else "Moderate" if self.difficulty == 3 else "Hard"

    def __str__(self): return f"{self.get_kind_display()}: {self.stem[:70]}"


def without_duplicates(questions):
    """Drop unsaved questions that repeat each other or an existing live question in the same scope.

    For bulk_create paths, which skip save(): fingerprints are filled in on the questions kept.
    """
    kept, seen = [], set()
    for question in questions:
        fingerprint = question.refresh_fingerprint()
        scope = ("paper", question.paper_id) if question.paper_id else ("tenant", question.tenant_id)
        if fingerprint and ((scope, fingerprint) in seen or question.duplicate()):
            continue
        if fingerprint:
            seen.add((scope, fingerprint))
        kept.append(question)
    return kept


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
