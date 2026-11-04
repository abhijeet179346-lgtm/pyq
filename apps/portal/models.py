import uuid
from django.conf import settings
from django.db import models


class DirectPracticeAttempt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="direct_practice_attempts")
    question = models.ForeignKey("content.Question", on_delete=models.PROTECT)
    question_version = models.PositiveIntegerField()
    submission_key = models.UUIDField(unique=True)
    selected_answers = models.JSONField(default=list)
    is_correct = models.BooleanField(default=False)
    time_taken_seconds = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [models.Index(fields=["user", "submitted_at"])]


class PracticeSession(models.Model):
    """A learner's practice run: an ordered question list plus their answers, kept so it can be resumed."""
    class Mode(models.TextChoices):
        LEARN = "learn", "Learn — feedback after each question"
        QUIZ = "quiz", "Quiz — feedback at the end"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="practice_sessions")
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="practice_sessions")
    title = models.CharField(max_length=200)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.LEARN)
    question_ids = models.JSONField(default=list)
    answers = models.JSONField(default=dict, blank=True, help_text="Position → submission key of the attempt.")
    revealed = models.JSONField(default=list, blank=True, help_text="Positions whose solution was viewed before answering.")
    browse_url = models.CharField(max_length=600, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["user", "updated_at"])]

    def __str__(self):
        return f"{self.title} ({len(self.question_ids)} questions)"


class PracticeBookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="practice_bookmarks")
    question = models.ForeignKey("content.Question", on_delete=models.CASCADE, related_name="bookmarks")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "question"], name="unique_practice_bookmark")]
