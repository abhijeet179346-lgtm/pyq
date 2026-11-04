from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class TestTemplate(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="test_templates")
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    duration_minutes = models.PositiveSmallIntegerField()
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self): return self.name


class TestSection(models.Model):
    template = models.ForeignKey(TestTemplate, on_delete=models.CASCADE, related_name="sections")
    name = models.CharField(max_length=120)
    order = models.PositiveSmallIntegerField(default=0)
    duration_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    question_count = models.PositiveSmallIntegerField(default=0)
    marking_scheme = models.JSONField(default=dict, help_text='Example: {"correct": 4, "incorrect": -1, "unanswered": 0}')

    class Meta:
        ordering = ["order", "id"]

    def clean(self):
        for key in ("correct", "incorrect", "unanswered"):
            if key not in self.marking_scheme:
                raise ValidationError({"marking_scheme": f"Missing {key} mark value."})


class TestQuestion(models.Model):
    section = models.ForeignKey(TestSection, on_delete=models.CASCADE, related_name="test_questions")
    question = models.ForeignKey("content.Question", on_delete=models.PROTECT)
    order = models.PositiveSmallIntegerField(default=0)
    points_override = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [models.UniqueConstraint(fields=["section", "question"], name="uniq_question_per_test_section")]


class TestAssignment(models.Model):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="test_assignments")
    template = models.ForeignKey(TestTemplate, on_delete=models.PROTECT, related_name="assignments")
    batch = models.ForeignKey("learning.Batch", on_delete=models.CASCADE, related_name="test_assignments")
    title = models.CharField(max_length=180)
    available_from = models.DateTimeField()
    deadline = models.DateTimeField()
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    active = models.BooleanField(default=True)
    shuffle_questions = models.BooleanField(default=False)
    shuffle_options = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-available_from"]

    def clean(self):
        if self.template_id and self.tenant_id != self.template.tenant_id:
            raise ValidationError("Assignment and template must belong to the same tenant.")
        if self.batch_id and self.tenant_id != self.batch.tenant_id:
            raise ValidationError("Assignment and batch must belong to the same tenant.")
        if self.deadline and self.available_from and self.deadline <= self.available_from:
            raise ValidationError({"deadline": "Deadline must be after the start time."})


class TestAttempt(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Submitted"
        EXPIRED = "expired", "Expired"

    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    assignment = models.ForeignKey(TestAssignment, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="test_attempts")
    attempt_number = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField()
    deadline = models.DateTimeField()
    current_section = models.ForeignKey(TestSection, null=True, blank=True, on_delete=models.SET_NULL, related_name="active_attempts")
    section_deadline = models.DateTimeField(null=True, blank=True)
    option_order = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    total_score = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    section_scores = models.JSONField(default=dict, blank=True)
    question_order = models.JSONField(default=list, blank=True)
    tab_switch_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["assignment", "student", "attempt_number"], name="uniq_assignment_student_attempt")]
        indexes = [models.Index(fields=["tenant", "student", "status"])]


class AttemptAnswer(models.Model):
    attempt = models.ForeignKey(TestAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey("content.Question", on_delete=models.PROTECT)
    answer = models.TextField(blank=True)
    marked_for_review = models.BooleanField(default=False)
    answered_at = models.DateTimeField(auto_now=True)
    time_spent_seconds = models.PositiveIntegerField(default=0)
    is_correct = models.BooleanField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "question"], name="uniq_attempt_question_answer")]


class TimedTest(models.Model):
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=models.CASCADE, related_name='timed_tests')
    exam = models.ForeignKey('catalog.Exam', on_delete=models.PROTECT)
    name = models.CharField(max_length=180)
    series = models.CharField(max_length=100, blank=True)
    paper_number = models.PositiveSmallIntegerField(default=1)
    subject = models.CharField(max_length=40, blank=True)
    duration_minutes = models.PositiveSmallIntegerField(default=180)
    instructions = models.TextField(blank=True)
    questions = models.JSONField(default=list, help_text='Question snapshots including answer keys and section marking rules. Use Excel import to create tests.')
    active = models.BooleanField(default=False)
    source = models.URLField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='custom_tests',
        help_text='Set for a learner-built custom test; only the owner can see it.')
    fullscreen_exit_limit = models.PositiveSmallIntegerField(null=True, blank=True,
        help_text='Auto-submit after this many full-screen exits or tab switches. Leave blank to warn and log only.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['series', 'paper_number', 'id']
        constraints = [models.UniqueConstraint(fields=['tenant','name'], name='unique_tenant_timed_test')]

    def __str__(self): return self.name

    @property
    def max_marks(self): return sum(float(q.get('marking',{}).get('correct',0)) for q in self.questions)

    def clean(self):
        from .timed_services import validate_questions
        if self.duration_minutes < 1 or self.duration_minutes > 360:raise ValidationError('Duration must be 1–360 minutes.')
        validate_questions(self.questions)


class TimedAttempt(models.Model):
    test = models.ForeignKey(TimedTest,on_delete=models.PROTECT,related_name='timed_attempts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    started_at = models.DateTimeField()
    deadline = models.DateTimeField()
    status = models.CharField(max_length=20,default='in_progress',choices=[('in_progress','In progress'),('submitted','Submitted'),('expired','Expired')])
    snapshot = models.JSONField(default=list)
    answers = models.JSONField(default=dict,blank=True)
    review = models.JSONField(default=list,blank=True)
    result = models.JSONField(default=dict,blank=True)
    score = models.DecimalField(max_digits=9,decimal_places=2,default=0)
    submitted_at = models.DateTimeField(null=True,blank=True)
    visited = models.JSONField(default=list,blank=True)
    time_spent = models.JSONField(default=dict,blank=True,help_text='Seconds spent on each question key.')
    events = models.JSONField(default=list,blank=True,help_text='Full-screen exits and tab switches during the attempt.')

    class Meta:
        constraints=[models.UniqueConstraint(fields=['test','user'],name='unique_user_timed_attempt')]
