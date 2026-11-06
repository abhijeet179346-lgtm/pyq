from django.core.exceptions import ValidationError
from django.db import models


class Exam(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True)
    active = models.BooleanField(default=True)
    subjects = models.ManyToManyField("Subject", related_name="exams", blank=True)
    def __str__(self): return self.name


class Subject(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(unique=True)
    def __str__(self): return self.name


class Chapter(models.Model):
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="chapters")
    name = models.CharField(max_length=160)
    order = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ["subject__name", "order", "name"]
        constraints = [models.UniqueConstraint(fields=["subject", "name"], name="uniq_chapter_subject_name")]
    def __str__(self): return f"{self.subject}: {self.name}"


class Topic(models.Model):
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="topics")
    name = models.CharField(max_length=160)
    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["chapter", "name"], name="uniq_topic_chapter_name")]
    def __str__(self): return f"{self.chapter} / {self.name}"


class Subtopic(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, related_name="subtopics")
    name = models.CharField(max_length=160)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        constraints = [models.UniqueConstraint(fields=["topic", "name"], name="uniq_subtopic_topic_name")]

    def __str__(self):
        return f"{self.topic} / {self.name}"


class Paper(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name="papers")
    year = models.PositiveSmallIntegerField()
    sitting = models.CharField(max_length=100, blank=True, help_text="For example: 29 Jan Shift 1")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["exam", "year", "sitting"], name="uniq_exam_paper_sitting")]
    def __str__(self): return f"{self.exam} {self.year} {self.sitting}".strip()
