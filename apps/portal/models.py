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
