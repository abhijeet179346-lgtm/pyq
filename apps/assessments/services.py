import random
from decimal import Decimal
from datetime import timedelta

from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.learning.answering import is_correct
from apps.tenancy.features import is_enabled
from apps.features.models import TenantExam
from apps.content.models import Question
from apps.audit.models import AuditEvent
from .models import AttemptAnswer, TestAttempt, TestQuestion


def _section_deadline(attempt, section, now):
    if section.duration_minutes:
        proposed = now + timedelta(minutes=section.duration_minutes)
        return min(proposed, attempt.deadline)
    return attempt.deadline


@transaction.atomic
def start_or_resume(assignment, student, *, integrity_enabled=False):
    now = timezone.now()
    attempt = TestAttempt.objects.filter(assignment=assignment, student=student).order_by("-attempt_number").first()
    if attempt:
        return attempt
    sections = list(assignment.template.sections.all())
    if not sections:
        raise ValueError("This test template has no sections yet.")
    for section in sections:
        count = section.test_questions.count()
        if section.question_count and count != section.question_count:
            raise ValueError(f"Section '{section.name}' expects {section.question_count} questions but has {count}.")
        if count == 0:
            raise ValueError(f"Section '{section.name}' has no questions.")
        for test_question in section.test_questions.select_related("question").all():
            question = test_question.question
            if question.status != Question.Status.PUBLISHED:
                raise ValueError(f"Section '{section.name}' contains an unpublished question.")
            if question.tenant_id not in (None, assignment.tenant_id):
                raise ValueError(f"Section '{section.name}' contains another tenant's question.")
            if question.tenant_id is not None and not is_enabled(assignment.tenant, "tenant_question_bank"):
                raise ValueError("Tenant-owned question access is disabled for this institute.")
            if question.tenant_id is None and not is_enabled(assignment.tenant, "global_pyq_bank"):
                raise ValueError("Global PYQ access is disabled for this institute.")
            if question.tenant_id is None and question.paper_id is None:
                raise ValueError(f"Section '{section.name}' contains a PYQ without a paper.")
            if question.tenant_id is None and not TenantExam.objects.filter(tenant=assignment.tenant,
                    exam_id=question.paper.exam_id, enabled=True).exists():
                raise ValueError("A test includes a global exam that this institute has not enabled.")
    deadline = min(now + timedelta(minutes=assignment.template.duration_minutes), assignment.deadline)
    try:
        with transaction.atomic():
            attempt = TestAttempt.objects.create(tenant=assignment.tenant, assignment=assignment, student=student,
                attempt_number=1, started_at=now, deadline=deadline, current_section=sections[0],
                section_deadline=_section_deadline_for_start(sections[0], deadline, now))
    except IntegrityError:
        attempt = TestAttempt.objects.filter(assignment=assignment, student=student).order_by("-attempt_number").first()
        if attempt:
            return attempt
        raise
    ids = list(TestQuestion.objects.filter(section__template=assignment.template).values_list("question_id", flat=True))
    if integrity_enabled and assignment.shuffle_questions:
        random.SystemRandom().shuffle(ids)
    attempt.question_order = ids
    if integrity_enabled and assignment.shuffle_options:
        option_orders = {}
        for question in assignment.template.sections.values_list("test_questions__question_id", flat=True):
            if question:
                from apps.content.models import Question
                q = Question.objects.filter(pk=question).first()
                if q and q.options:
                    order = list(range(len(q.options)))
                    random.SystemRandom().shuffle(order)
                    option_orders[str(question)] = order
        attempt.option_order = option_orders
    attempt.save(update_fields=["question_order", "option_order"])
    AuditEvent.objects.create(tenant=assignment.tenant, actor=student, action="test_attempt_started",
        object_type="TestAttempt", object_id=str(attempt.pk), after={"assignment_id": assignment.pk, "deadline": attempt.deadline.isoformat()})
    return attempt


def _section_deadline_for_start(section, overall_deadline, now):
    if section.duration_minutes:
        return min(now + timedelta(minutes=section.duration_minutes), overall_deadline)
    return overall_deadline


def _next_section(attempt, now):
    sections = list(attempt.assignment.template.sections.all())
    current_index = next((i for i, section in enumerate(sections) if section.pk == attempt.current_section_id), -1)
    next_index = current_index + 1
    if next_index >= len(sections):
        return False
    section = sections[next_index]
    attempt.current_section = section
    attempt.section_deadline = _section_deadline(attempt, section, now)
    attempt.save(update_fields=["current_section", "section_deadline"])
    return True


@transaction.atomic
def save_section_answers(attempt, post_data):
    now = timezone.now()
    if attempt.status != TestAttempt.Status.IN_PROGRESS:
        return "completed"
    if now >= attempt.deadline:
        finalize_attempt(attempt)
        return "completed"
    if attempt.section_deadline and now >= attempt.section_deadline:
        if not _next_section(attempt, now):
            finalize_attempt(attempt)
            return "completed"
        return "advanced"
    test_questions = TestQuestion.objects.filter(section=attempt.current_section).select_related("question")
    for item in test_questions:
        field = f"answer_{item.question_id}"
        answers = post_data.getlist(field) if hasattr(post_data, "getlist") else [post_data.get(field, "")]
        answer_text = ", ".join(value for value in answers if value)
        review = str(post_data.get(f"review_{item.question_id}", "")).lower() in ("1", "true", "on", "yes")
        try: duration = max(0, min(int(post_data.get(f"time_{item.question_id}", "0")), 3600))
        except (ValueError, TypeError): duration = 0
        correct = is_correct(item.question, answers) if answer_text else None
        AttemptAnswer.objects.update_or_create(attempt=attempt, question=item.question,
            defaults={"answer": answer_text, "marked_for_review": review,
                      "time_spent_seconds": duration, "is_correct": correct})
    try:
        attempt.tab_switch_count = min(max(int(post_data.get("tab_switch_count", attempt.tab_switch_count)), 0), 1000)
    except (ValueError, TypeError):
        pass
    attempt.save(update_fields=["tab_switch_count"])
    return "saved"


@transaction.atomic
def advance_section(attempt):
    if attempt.status != TestAttempt.Status.IN_PROGRESS:
        return False
    now = timezone.now()
    if now >= attempt.deadline:
        finalize_attempt(attempt)
        return False
    if not _next_section(attempt, now):
        finalize_attempt(attempt)
        return False
    return True


@transaction.atomic
def finalize_attempt(attempt):
    attempt.refresh_from_db()
    if attempt.status != TestAttempt.Status.IN_PROGRESS:
        return attempt
    section_scores = {}
    total = Decimal("0")
    answers = {item.question_id: item for item in attempt.answers.all()}
    from apps.tutoring.services import record_question_outcome
    for section in attempt.assignment.template.sections.all():
        scheme = section.marking_scheme
        correct_mark = Decimal(str(scheme.get("correct", 0)))
        incorrect_mark = Decimal(str(scheme.get("incorrect", 0)))
        unanswered_mark = Decimal(str(scheme.get("unanswered", 0)))
        score = Decimal("0")
        correct_count = incorrect_count = unanswered_count = 0
        for item in section.test_questions.select_related("question").all():
            answer = answers.get(item.question_id)
            if answer is None or not answer.answer.strip():
                score += unanswered_mark
                unanswered_count += 1
            elif is_correct(item.question, [part.strip() for part in answer.answer.split(",")]):
                score += item.points_override if item.points_override is not None else correct_mark
                correct_count += 1
                answer.is_correct = True
                answer.save(update_fields=["is_correct"])
                record_question_outcome(tenant=attempt.tenant, student=attempt.student, question=item.question,
                    correct=True, test_answer=answer)
            else:
                score += incorrect_mark
                incorrect_count += 1
                answer.is_correct = False
                answer.save(update_fields=["is_correct"])
                record_question_outcome(tenant=attempt.tenant, student=attempt.student, question=item.question,
                    correct=False, test_answer=answer)
        section_scores[str(section.pk)] = {"name": section.name, "score": str(score), "correct": correct_count,
                                          "incorrect": incorrect_count, "unanswered": unanswered_count}
        total += score
    attempt.total_score = total
    attempt.section_scores = section_scores
    attempt.status = TestAttempt.Status.SUBMITTED if timezone.now() < attempt.deadline else TestAttempt.Status.EXPIRED
    attempt.submitted_at = timezone.now()
    attempt.current_section = None
    attempt.section_deadline = None
    attempt.save(update_fields=["total_score", "section_scores", "status", "submitted_at", "current_section", "section_deadline"])
    AuditEvent.objects.create(tenant=attempt.tenant, actor=attempt.student, action="test_attempt_scored",
        object_type="TestAttempt", object_id=str(attempt.pk), after={"status": attempt.status,
        "total_score": str(attempt.total_score), "section_scores": section_scores})
    return attempt
