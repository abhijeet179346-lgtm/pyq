from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import JsonResponse
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.learning.answering import option_pairs
from apps.learning.models import BatchEnrollment, TeacherBatchAssignment, PracticeAttempt
from apps.learning.views import _can_use_batch, _tenant_user
from apps.tenancy.features import is_enabled
from apps.features.models import TenantExam
from .models import AttemptAnswer, TestAssignment, TestAttempt, TestQuestion
from .services import advance_section, finalize_attempt, save_section_answers, start_or_resume


def _allow_assignment(user, assignment):
    return _can_use_batch(user, assignment.batch)


@login_required
@require_http_methods(["GET"])
def assignment_list(request):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "tests") or not is_enabled(tenant, "custom_test_assignment"):
        raise PermissionDenied("Tests are disabled for this institute.")
    qs = TestAssignment.objects.filter(tenant=tenant, active=True)
    if request.user.role == "student":
        batch_ids = BatchEnrollment.objects.filter(student=request.user, active=True).values_list("batch_id", flat=True)
        qs = qs.filter(batch_id__in=batch_ids)
    elif request.user.role == "teacher":
        batch_ids = TeacherBatchAssignment.objects.filter(teacher=request.user).values_list("batch_id", flat=True)
        qs = qs.filter(batch_id__in=batch_ids)
    elif request.user.role != "tenant_admin" and not request.user.is_superuser:
        raise PermissionDenied("Tests are not available for this account.")
    return render(request, "assessments/assignment_list.html", {"assignments": qs.select_related("template", "batch")})


@login_required
@require_http_methods(["GET", "POST"])
def start_assignment(request, assignment_id):
    tenant = _tenant_user(request)
    subscription = getattr(tenant, "subscription", None)
    if subscription and subscription.status not in ("trial", "active"):
        raise PermissionDenied("This institute is in read-only billing grace; new tests cannot be started.")
    if not is_enabled(tenant, "tests"):
        raise PermissionDenied("Tests are disabled for this institute.")
    assignment = get_object_or_404(TestAssignment.objects.select_related("template", "batch"),
                                   pk=assignment_id, tenant=tenant, active=True)
    if not _allow_assignment(request.user, assignment):
        raise PermissionDenied("This test is not assigned to one of your batches.")
    if not is_enabled(tenant, "custom_test_assignment"):
        raise PermissionDenied("Assigned tests are disabled for this institute.")
    now = timezone.now()
    if not assignment.available_from <= now <= assignment.deadline:
        raise PermissionDenied("This test is outside its availability window.")
    try:
        attempt = start_or_resume(assignment, request.user,
                                  integrity_enabled=is_enabled(tenant, "test_integrity"))
    except ValueError as exc:
        from django.http import HttpResponse
        return HttpResponse(str(exc), status=409, content_type="text/plain; charset=utf-8")
    return redirect("test_attempt", attempt_id=attempt.pk)


def _attempt_questions(attempt):
    items = list(TestQuestion.objects.filter(section=attempt.current_section).select_related("question__topic__chapter__subject"))
    order = {question_id: i for i, question_id in enumerate(attempt.question_order)}
    items.sort(key=lambda item: order.get(item.question_id, len(order) + item.order))
    saved = {answer.question_id: answer for answer in attempt.answers.filter(question__in=[item.question for item in items])}
    result = []
    for item in items:
        q = item.question
        if q.tenant_id is None and q.paper_id is None:
            raise PermissionDenied("This test contains a PYQ without a paper assignment.")
        if q.tenant_id is None and not is_enabled(attempt.tenant, "global_pyq_bank"):
            raise PermissionDenied("This attempt includes global questions but the tenant's PYQ feature is disabled.")
        if q.tenant_id is None and not TenantExam.objects.filter(tenant=attempt.tenant, exam_id=q.paper.exam_id, enabled=True).exists():
            raise PermissionDenied("This question's exam is not enabled for the tenant.")
        if q.tenant_id is not None and not is_enabled(attempt.tenant, "tenant_question_bank"):
            raise PermissionDenied("Tenant question access is disabled for this institute.")
        if q.tenant_id not in (None, attempt.tenant_id) or q.status != q.Status.PUBLISHED:
            raise PermissionDenied("This test contains unavailable content.")
        opts = option_pairs(q)
        indices = attempt.option_order.get(str(q.pk))
        if indices:
            opts = [opts[index] for index in indices if index < len(opts)]
        result.append({"item": item, "question": q, "options": opts, "saved": saved.get(q.pk)})
    return result


@login_required
@require_http_methods(["GET", "POST"])
def test_attempt(request, attempt_id):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "tests"):
        raise PermissionDenied("Tests are disabled for this institute.")
    attempt = get_object_or_404(TestAttempt.objects.select_related("assignment__batch", "assignment__template", "current_section"),
                                pk=attempt_id, tenant=tenant, student=request.user)
    subscription = getattr(tenant, "subscription", None)
    if subscription and subscription.status == "grace" and attempt.status == TestAttempt.Status.IN_PROGRESS:
        finalize_attempt(attempt)
        return redirect("test_result", attempt_id=attempt.pk)
    if not _allow_assignment(request.user, attempt.assignment):
        raise PermissionDenied("This attempt is outside your assigned batch.")
    if attempt.status != TestAttempt.Status.IN_PROGRESS:
        return redirect("test_result", attempt_id=attempt.pk)
    now = timezone.now()
    if now >= attempt.deadline:
        finalize_attempt(attempt)
        return redirect("test_result", attempt_id=attempt.pk)
    if attempt.section_deadline and now >= attempt.section_deadline:
        if not advance_section(attempt):
            return redirect("test_result", attempt_id=attempt.pk)
        return redirect("test_attempt", attempt_id=attempt.pk)
    if request.method == "POST":
        submitted = request.POST
        integrity_enabled = is_enabled(tenant, "test_integrity")
        if not integrity_enabled:
            submitted = request.POST.copy()
            submitted.pop("tab_switch_count", None)
        state = save_section_answers(attempt, submitted)
        attempt.refresh_from_db()
        action = request.POST.get("action", "save")
        if state == "completed" or attempt.status != TestAttempt.Status.IN_PROGRESS:
            if action == "save":
                return JsonResponse({"state": "completed", "result_url": redirect("test_result", attempt_id=attempt.pk).url})
            return redirect("test_result", attempt_id=attempt.pk)
        if state == "advanced":
            if action == "save":
                return JsonResponse({"state": "advanced", "reload_url": redirect("test_attempt", attempt_id=attempt.pk).url})
            return redirect("test_attempt", attempt_id=attempt.pk)
        if action == "next":
            if not advance_section(attempt):
                return redirect("test_result", attempt_id=attempt.pk)
            return redirect("test_attempt", attempt_id=attempt.pk)
        if action == "submit":
            finalize_attempt(attempt)
            return redirect("test_result", attempt_id=attempt.pk)
        return JsonResponse({"state": "saved", "section_deadline": attempt.section_deadline.isoformat() if attempt.section_deadline else None})
    sections = list(attempt.assignment.template.sections.all())
    current_index = next((i for i, section in enumerate(sections) if section.pk == attempt.current_section_id), 0)
    return render(request, "assessments/attempt.html", {
        "attempt": attempt, "assignment": attempt.assignment, "section": attempt.current_section,
        "items": _attempt_questions(attempt), "answers": {str(answer.question_id): answer for answer in attempt.answers.all()},
        "question_count": TestQuestion.objects.filter(section=attempt.current_section).count(),
        "integrity_enabled": is_enabled(tenant, "test_integrity"),
        "has_next_section": current_index < len(sections) - 1,
    })


@login_required
@require_http_methods(["GET"])
def test_result(request, attempt_id):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "tests"):
        raise PermissionDenied("Tests are disabled for this institute.")
    attempt = get_object_or_404(TestAttempt.objects.select_related("assignment__batch", "assignment__template"),
                                pk=attempt_id, tenant=tenant, student=request.user)
    if not _allow_assignment(request.user, attempt.assignment):
        raise PermissionDenied("This result is outside your assigned batch.")
    if attempt.status == TestAttempt.Status.IN_PROGRESS:
        return redirect("test_attempt", attempt_id=attempt.pk)
    completed = list(TestAttempt.objects.filter(assignment=attempt.assignment,
        status__in=[TestAttempt.Status.SUBMITTED, TestAttempt.Status.EXPIRED]).order_by("-total_score"))
    rank = next((index for index, item in enumerate(completed, start=1) if item.pk == attempt.pk), None)
    percentile = round(100 * sum(1 for item in completed if item.total_score <= attempt.total_score) / len(completed), 1) if completed else 0
    tenant_cohort = list(TestAttempt.objects.filter(tenant=tenant, assignment__template=attempt.assignment.template,
        status__in=[TestAttempt.Status.SUBMITTED, TestAttempt.Status.EXPIRED]).order_by("-total_score"))
    tenant_rank = next((index for index, item in enumerate(tenant_cohort, start=1) if item.pk == attempt.pk), None)
    answers = list(AttemptAnswer.objects.filter(attempt=attempt).select_related("question__topic__chapter__subject").order_by("question_id"))
    chapter_stats = {}
    for answer in answers:
        chapter = answer.question.topic.chapter.name if answer.question.topic_id else "Unclassified"
        row = chapter_stats.setdefault(chapter, {"attempted": 0, "correct": 0, "seconds": 0})
        row["attempted"] += 1
        row["correct"] += 1 if answer.is_correct else 0
        row["seconds"] += answer.time_spent_seconds
    for chapter, row in chapter_stats.items():
        row["accuracy"] = round(100 * row["correct"] / row["attempted"], 1) if row["attempted"] else 0
        row["average_seconds"] = round(row["seconds"] / row["attempted"], 1) if row["attempted"] else 0
    from apps.tutoring.models import ConceptMastery
    weakest = ConceptMastery.objects.filter(tenant=tenant, student=request.user).select_related(
        "concept_tag__topic__chapter__subject").order_by("score")[:5]
    return render(request, "assessments/result.html", {"attempt": attempt, "rank": rank,
                                                         "percentile": percentile, "cohort_size": len(completed),
                                                         "answers": answers,
                                                         "mistake_tutor_enabled": is_enabled(tenant, "ai_tutor_mistake"),
                                                         "tenant_rank": tenant_rank, "tenant_cohort_size": len(tenant_cohort),
                                                         "chapter_stats": chapter_stats.items(), "weakest": weakest})


@login_required
@require_http_methods(["GET"])
def batch_results_export(request, batch_id):
    tenant = _tenant_user(request, roles=("tenant_admin", "teacher") if not request.user.is_superuser else None)
    if not is_enabled(tenant, "result_export"):
        raise PermissionDenied("Result export is disabled for this institute.")
    batch = get_object_or_404(tenant.batches, pk=batch_id)
    if request.user.role == "teacher" and not TeacherBatchAssignment.objects.filter(teacher=request.user, batch=batch).exists():
        raise PermissionDenied("This batch is not assigned to you.")
    import csv
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="batch-{batch.pk}-results.csv"'
    writer = csv.writer(response)
    writer.writerow(["student_email", "student_name", "tests_completed", "latest_test_score", "practice_count", "practice_accuracy_percent"])
    for enrollment in batch.enrollments.filter(active=True).select_related("student").order_by("student__email"):
        student = enrollment.student
        attempts = TestAttempt.objects.filter(tenant=tenant, assignment__batch=batch, student=student,
            status__in=[TestAttempt.Status.SUBMITTED, TestAttempt.Status.EXPIRED]).order_by("-submitted_at")
        practice = PracticeAttempt.objects.filter(tenant=tenant, batch=batch, student=student)
        count = practice.count()
        accuracy = round(100 * practice.filter(is_correct=True).count() / count, 1) if count else ""
        writer.writerow([student.email, student.get_full_name(), attempts.count(), attempts.values_list("total_score", flat=True).first() or "", count, accuracy])
    return response


@login_required
@require_http_methods(["GET"])
def batch_results(request, batch_id):
    tenant = _tenant_user(request, roles=("tenant_admin", "teacher") if not request.user.is_superuser else None)
    if not is_enabled(tenant, "batch_analytics"):
        raise PermissionDenied("Batch analytics are disabled for this institute.")
    batch = get_object_or_404(tenant.batches, pk=batch_id, active=True)
    if request.user.role == "teacher" and not TeacherBatchAssignment.objects.filter(teacher=request.user, batch=batch).exists():
        raise PermissionDenied("This batch is not assigned to you.")
    enrollments = batch.enrollments.filter(active=True).select_related("student").order_by("student__last_name", "student__first_name")
    roster = []
    for enrollment in enrollments:
        student = enrollment.student
        attempts = TestAttempt.objects.filter(tenant=tenant, student=student, assignment__batch=batch,
                                              status__in=[TestAttempt.Status.SUBMITTED, TestAttempt.Status.EXPIRED])
        practice = PracticeAttempt.objects.filter(tenant=tenant, student=student, batch=batch)
        roster.append({"student": student, "test_count": attempts.count(),
                       "latest_score": attempts.order_by("-submitted_at").values_list("total_score", flat=True).first(),
                       "practice_count": practice.count(),
                       "practice_accuracy": round(100 * practice.filter(is_correct=True).count() / practice.count(), 1) if practice.exists() else None})
    return render(request, "assessments/batch_results.html", {"batch": batch, "roster": roster})
