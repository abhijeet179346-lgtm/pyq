from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from collections import Counter

from apps.catalog.models import Topic
from apps.content.models import Question
from apps.features.models import TenantExam
from apps.learning.models import TeacherBatchAssignment, TeacherSubjectAssignment
from apps.learning.views import _tenant_user
from apps.tenancy.features import is_enabled
from apps.tutoring.models import MistakeDiagnosis
from apps.tutoring.services import generate_study_questions
from .models import MaterialSection, VideoMapping


@login_required
@require_http_methods(["GET", "POST"])
def ncert_studio(request, topic_id):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "ncert_studio") or not is_enabled(tenant, "study_material"):
        raise PermissionDenied("NCERT Studio is disabled for this institute.")
    topic = get_object_or_404(Topic.objects.select_related("chapter__subject"), pk=topic_id)
    if request.user.role == "teacher" and not TeacherSubjectAssignment.objects.filter(
            tenant=tenant, teacher=request.user, subject=topic.chapter.subject).exists():
        raise PermissionDenied("You are not assigned to this subject.")
    if request.method == "POST":
        if request.user.role not in ("tenant_admin", "teacher") and not request.user.is_superuser:
            raise PermissionDenied("Only admins and teachers may generate question drafts.")
        if not is_enabled(tenant, "ai_question_generation"):
            raise PermissionDenied("AI question generation is disabled for this institute.")
        if request.user.role == "teacher" and not TeacherBatchAssignment.objects.filter(
                teacher=request.user, batch__tenant=tenant).exists():
            raise PermissionDenied("You are not assigned to an institute batch.")
        materials = list(MaterialSection.objects.filter(topic=topic, active=True, document__active=True).filter(
            Q(document__tenant=tenant) | Q(document__tenant__isnull=True)).select_related("document")[:4])
        if not materials:
            messages.error(request, "Add study material for this topic before generating questions.")
        else:
            generated = generate_study_questions(tenant=tenant, user=request.user, topic=topic, material_sections=materials)
            messages.success(request, f"Created {len(generated)} unpublished drafts for teacher review.")
    sections = MaterialSection.objects.filter(topic=topic, active=True, document__active=True).filter(
        Q(document__tenant=tenant) | Q(document__tenant__isnull=True)).select_related("document")
    question_scope = Q(pk__isnull=True)
    if is_enabled(tenant, "tenant_question_bank"):
        question_scope |= Q(tenant=tenant)
    if is_enabled(tenant, "global_pyq_bank"):
        enabled_exams = TenantExam.objects.filter(tenant=tenant, enabled=True).values_list("exam_id", flat=True)
        question_scope |= Q(tenant__isnull=True, paper__exam_id__in=enabled_exams)
    questions = Question.objects.filter(topic=topic, status=Question.Status.PUBLISHED).filter(question_scope).select_related("paper")[:50]
    videos = VideoMapping.objects.filter(concept_tag__topic=topic, active=True).filter(
        Q(tenant=tenant) | Q(tenant__isnull=True)).select_related("concept_tag")
    return render(request, "materials/studio.html", {"topic": topic, "sections": sections,
        "questions": questions, "videos": videos, "question_generation_enabled": is_enabled(tenant, "ai_question_generation")})


@login_required
@require_http_methods(["GET"])
def projector_mode(request, batch_id):
    tenant = _tenant_user(request, roles=("teacher", "tenant_admin") if not request.user.is_superuser else None)
    if not is_enabled(tenant, "projector_mode"):
        raise PermissionDenied("Projector mode is disabled.")
    from apps.learning.models import Batch
    batch = get_object_or_404(Batch, pk=batch_id, tenant=tenant, active=True)
    if request.user.role == "teacher" and not TeacherBatchAssignment.objects.filter(teacher=request.user, batch=batch).exists():
        raise PermissionDenied("This batch is not assigned to you.")
    topic = get_object_or_404(Topic, pk=request.GET.get("topic")) if request.GET.get("topic") else None
    if request.user.role == "teacher" and topic and not TeacherSubjectAssignment.objects.filter(
            tenant=tenant, teacher=request.user, subject=topic.chapter.subject).exists():
        raise PermissionDenied("You are not assigned to this subject.")
    students = batch.enrollments.filter(active=True).values_list("student_id", flat=True)
    from apps.assessments.models import AttemptAnswer
    from apps.learning.models import PracticeAttempt
    mistakes = Counter()
    practice_wrong = PracticeAttempt.objects.filter(tenant=tenant, batch=batch, is_correct=False)
    test_wrong = AttemptAnswer.objects.filter(attempt__tenant=tenant, attempt__assignment__batch=batch, is_correct=False)
    if topic:
        practice_wrong = practice_wrong.filter(question__topic=topic)
        test_wrong = test_wrong.filter(question__topic=topic)
    mistakes.update(practice_wrong.values_list("question_id", flat=True))
    mistakes.update(test_wrong.values_list("question_id", flat=True))
    question_ids = [question_id for question_id, _ in mistakes.most_common(5)]
    question_map = Question.objects.filter(pk__in=question_ids, status=Question.Status.PUBLISHED).select_related("topic")
    questions = sorted(question_map, key=lambda question: question_ids.index(question.pk))
    sections = MaterialSection.objects.filter(active=True, document__active=True).filter(
        Q(document__tenant=tenant) | Q(document__tenant__isnull=True))
    if topic: sections = sections.filter(topic=topic)
    else: sections = sections.none()
    return render(request, "materials/projector.html", {"batch": batch, "topic": topic,
        "questions": questions, "sections": sections[:3], "topics": Topic.objects.order_by("name")})
