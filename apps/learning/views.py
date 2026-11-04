import io
import csv
import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import FileResponse, HttpResponse, Http404
from django.core.files.storage import default_storage
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.catalog.models import Subject, Chapter
from apps.catalog.browsing import taxonomy_filters
from apps.content.models import ContentReport, Question
from apps.tenancy.features import is_enabled
from apps.audit.models import AuditEvent
from apps.features.models import TenantExam
from .answering import is_correct, option_pairs
from .forms import AccessCodeIssueForm, TeacherAssignmentForm
from .models import AccessCode, Batch, BatchEnrollment, PracticeAttempt, TeacherBatchAssignment, TeacherSubjectAssignment
from .services import issue_codes


def _tenant_user(request, roles=None):
    tenant = getattr(request, "tenant", None)
    user = request.user
    if not user.is_authenticated or tenant is None or (not user.is_superuser and user.tenant_id != tenant.pk):
        raise PermissionDenied("Sign in on this institute's subdomain.")
    if roles and user.role not in roles:
        raise PermissionDenied("This action is not available for your account role.")
    return tenant


def _can_use_batch(user, batch):
    if user.is_superuser:
        return True
    if user.tenant_id != batch.tenant_id:
        return False
    if user.role == "tenant_admin":
        return True
    if user.role == "student":
        return BatchEnrollment.objects.filter(student=user, batch=batch, tenant_id=user.tenant_id, active=True).exists()
    if user.role == "teacher":
        return TeacherBatchAssignment.objects.filter(teacher=user, batch=batch, tenant_id=user.tenant_id).exists()
    return False


@require_http_methods(["GET", "POST"])
def join(request):
    # Registration and institute activation are separate, ordered steps.
    if request.user.is_authenticated:
        return redirect("activate_account")
    return redirect("portal_login")


@login_required
@require_http_methods(["GET", "POST"])
def access_code_export(request):
    tenant = _tenant_user(request, roles=("tenant_admin",))
    if not is_enabled(tenant, "access_code_enrollment"):
        raise PermissionDenied("Access-code enrollment is disabled.")
    if request.method == "GET":
        return render(request, "learning/access_codes.html", {"form": AccessCodeIssueForm(tenant=tenant)})
    form = AccessCodeIssueForm(request.POST, tenant=tenant)
    if not form.is_valid():
        return render(request, "learning/access_codes.html", {"form": form}, status=400)
    data = form.cleaned_data
    if data["expires_at"] and data["expires_at"] <= timezone.now():
        form.add_error("expires_at", "Expiry must be in the future.")
        return render(request, "learning/access_codes.html", {"form": form}, status=400)
    issued = issue_codes(tenant=tenant, batch=data["batch"], creator=request.user,
                         count=data["count"], shared=data["shared"], max_uses=data["max_uses"],
                         role=data["role"], expires_at=data["expires_at"])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["access_code", "tenant", "batch", "role", "max_uses", "expires_at"])
    for access, code in issued:
        writer.writerow([code, tenant.slug, access.batch.name, access.role, access.max_uses, access.expires_at or ""])
    response = HttpResponse("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="pyq-vault-access-codes.csv"'
    return response


@login_required
@require_http_methods(["GET"])
def practice_home(request):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "practice"):
        raise PermissionDenied("Practice is disabled for this institute.")
    if not request.user.is_superuser and request.user.role == "student" and not BatchEnrollment.objects.filter(student=request.user, active=True).exists():
        raise PermissionDenied("You are not enrolled in an active batch.")
    if not request.user.is_superuser and request.user.role == "teacher" and not TeacherBatchAssignment.objects.filter(teacher=request.user).exists():
        raise PermissionDenied("You are not assigned to an active batch.")
    visible = Q(pk__isnull=True)
    if is_enabled(tenant, "tenant_question_bank"):
        visible |= Q(kind=Question.Kind.PRACTICE, tenant=tenant)
    if is_enabled(tenant, "global_pyq_bank"):
        enabled_exams = TenantExam.objects.filter(tenant=tenant, enabled=True, exam__active=True).values_list("exam_id", flat=True)
        visible |= Q(kind=Question.Kind.PYQ, tenant__isnull=True, paper__exam_id__in=enabled_exams)
    questions = Question.objects.filter(visible, status=Question.Status.PUBLISHED).select_related("topic__chapter__subject", "paper__exam", "subtopic")
    if request.user.role == "teacher":
        subjects = TeacherSubjectAssignment.objects.filter(tenant=tenant, teacher=request.user).values_list("subject_id", flat=True)
        questions = questions.filter(topic__chapter__subject_id__in=subjects)
    exam_id = request.GET.get("exam", "")
    exams = list(questions.exclude(paper__isnull=True).values("paper__exam_id", "paper__exam__name").distinct().order_by("paper__exam__name"))
    if exam_id:
        if not (exam_id.isascii() and exam_id.isdecimal() and len(exam_id) <= 18) or not any(str(exam["paper__exam_id"]) == exam_id for exam in exams):
            raise Http404("Exam unavailable.")
        questions = questions.filter(paper__exam_id=int(exam_id))
    questions, taxonomy = taxonomy_filters(questions, request.GET)
    difficulty=request.GET.get("difficulty", "")
    levels={"easy":[1,2], "moderate":[3], "hard":[4,5]}
    if difficulty:
        if difficulty not in levels:raise Http404("Invalid difficulty.")
        questions=questions.filter(difficulty__in=levels[difficulty])

    if request.user.role == "student":
        batches = Batch.objects.filter(enrollments__student=request.user, enrollments__active=True, active=True)
    elif request.user.role == "teacher":
        batches = Batch.objects.filter(teacher_assignments__teacher=request.user, active=True)
    else:
        batches = Batch.objects.filter(tenant=tenant, active=True)
    flags = {key: is_enabled(tenant, key) for key in (
        "tests", "access_code_enrollment", "branding_settings", "bulk_question_import",
        "batch_management", "global_pyq_bank", "teacher_assignments", "batch_analytics",
        "ai_tutor_mistake", "ai_tutor_chat", "photo_question_input", "mastery_analytics",
        "material_import", "scan_import", "ncert_studio", "projector_mode", "question_reporting")}
    return render(request, "learning/practice_home.html", {"tenant": tenant, "questions": questions[:100], "batches": batches,
        "flags": flags, **taxonomy, "exams": exams, "selected_exam": exam_id,"selected_difficulty":difficulty})


@login_required
@require_http_methods(["GET", "POST"])
def practice_question(request, question_id):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "practice"):
        raise PermissionDenied("Practice is disabled for this institute.")
    question = get_object_or_404(Question.objects.select_related("topic__chapter__subject", "paper__exam", "subtopic"),
                                 pk=question_id, status=Question.Status.PUBLISHED)
    if question.tenant_id == tenant.pk:
        if question.kind != Question.Kind.PRACTICE or not is_enabled(tenant, "tenant_question_bank"):
            raise PermissionDenied("Tenant questions are unavailable.")
    elif question.tenant_id is None:
        if (question.kind != Question.Kind.PYQ or not is_enabled(tenant, "global_pyq_bank") or
                not TenantExam.objects.filter(tenant=tenant, exam_id=question.paper.exam_id, enabled=True).exists()):
            raise PermissionDenied("Global PYQs are unavailable.")
    else:
        raise PermissionDenied("Question belongs to another institute.")
    if request.user.role == "teacher" and question.topic_id and not TeacherSubjectAssignment.objects.filter(
            tenant=tenant, teacher=request.user, subject=question.topic.chapter.subject).exists():
        raise PermissionDenied("You are not assigned to this question's subject.")
    batch_qs = Batch.objects.none()
    if request.user.role == "student":
        batch_qs = Batch.objects.filter(enrollments__student=request.user, enrollments__active=True, active=True)
    elif request.user.role == "teacher":
        batch_qs = Batch.objects.filter(teacher_assignments__teacher=request.user, active=True)
    elif request.user.role == "tenant_admin" or request.user.is_superuser:
        batch_qs = Batch.objects.filter(tenant=tenant, active=True)
    selected_batch = batch_qs.filter(pk=request.POST.get("batch") or request.GET.get("batch")).first()
    if request.user.role in ("student", "teacher") and selected_batch is None:
        selected_batch = batch_qs.first()
    if request.user.role in ("student", "teacher") and selected_batch is None:
        raise PermissionDenied("You are not enrolled or assigned to an active batch.")
    available_batches = batch_qs
    feedback = None
    if request.method == "POST":
        selected = request.POST.getlist("answer")
        correct = is_correct(question, selected)
        try: time_taken = min(max(int(request.POST.get("time_taken_seconds", "0")), 0), 3600)
        except ValueError: time_taken = 0
        practice_attempt = PracticeAttempt.objects.create(tenant=tenant, student=request.user, question=question,
                                       batch=selected_batch,
                                       question_version=question.version, selected_answer=", ".join(selected),
                                       is_correct=correct, time_taken_seconds=time_taken)
        from apps.tutoring.services import record_question_outcome
        record_question_outcome(tenant=tenant, student=request.user, question=question, correct=correct,
            practice_attempt=practice_attempt)
        feedback = {"correct": correct, "selected": selected}
    return render(request, "learning/practice_question.html", {
        "question": question, "options": option_pairs(question), "feedback": feedback, "tenant": tenant,
        "selected_batch": selected_batch, "available_batches": available_batches,
        "mistake_tutor_enabled": is_enabled(tenant, "ai_tutor_mistake"),
        "mistake_attempt_id": practice_attempt.pk if request.method == "POST" else None,
        "reporting_enabled": is_enabled(tenant, "question_reporting"),
    })


@login_required
@require_http_methods(["POST"])
def report_question(request, question_id):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "question_reporting"):
        raise PermissionDenied("Question reporting is disabled for this institute.")
    question = get_object_or_404(Question, pk=question_id, status=Question.Status.PUBLISHED)
    if question.tenant_id not in (None, tenant.pk):
        raise PermissionDenied("Question belongs to another institute.")
    if question.tenant_id is None and (question.paper_id is None or not TenantExam.objects.filter(
            tenant=tenant, exam_id=question.paper.exam_id, enabled=True).exists()):
        raise PermissionDenied("This exam is not enabled for your institute.")
    reason = request.POST.get("reason", "").strip()[:1000]
    if not reason:
        messages.error(request, "Add a short reason for the report.")
        return redirect("practice_question", question_id=question.pk)
    _, created = ContentReport.objects.get_or_create(question=question, reporter=request.user,
        defaults={"tenant": tenant, "reason": reason})
    if created:
        total = question.reports.count()
        if question.kind == Question.Kind.PYQ and total >= 3:
            question.status = Question.Status.IN_REVIEW
            question.save(update_fields=["status", "updated_at"])
        AuditEvent.objects.create(tenant=tenant, actor=request.user, action="content_question_reported",
            object_type="Question", object_id=str(question.pk), after={"report_count": total,
            "pending_review": question.status == Question.Status.IN_REVIEW})
        messages.success(request, "Report sent to the content review queue.")
    else:
        messages.info(request, "You already reported this question.")
    return redirect("practice_question", question_id=question.pk)


@login_required
@require_http_methods(["GET"])
def question_source_image(request, question_id):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "practice") or not is_enabled(tenant, "tenant_question_bank"):
        raise PermissionDenied("Question source images are unavailable.")
    question = get_object_or_404(Question, pk=question_id, tenant=tenant,
        status=Question.Status.PUBLISHED, source_scan_id__isnull=False, source_page__isnull=False)
    if request.user.role == "student" and not BatchEnrollment.objects.filter(student=request.user, tenant=tenant, active=True).exists():
        raise PermissionDenied("You are not enrolled in an active batch.")
    if request.user.role == "teacher" and not TeacherSubjectAssignment.objects.filter(
            tenant=tenant, teacher=request.user, subject=question.topic.chapter.subject).exists():
        raise PermissionDenied("You are not assigned to this question's subject.")
    from apps.scans.models import ScanImport
    scan = get_object_or_404(ScanImport, pk=question.source_scan_id, tenant=tenant)
    if question.source_page < 1 or question.source_page > len(scan.page_image_paths):
        raise Http404("Question source image not found.")
    path = scan.page_image_paths[question.source_page - 1]
    if not path or not default_storage.exists(path):
        raise Http404("Question source image not found.")
    return FileResponse(default_storage.open(path, "rb"),
        content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")


@login_required
@require_http_methods(["GET"])
def question_figure_image(request, question_id, figure_index):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "practice") or not is_enabled(tenant, "tenant_question_bank"):
        raise PermissionDenied("Question figures are unavailable.")
    question = get_object_or_404(Question, pk=question_id, tenant=tenant, status=Question.Status.PUBLISHED)
    if request.user.role == "student" and not BatchEnrollment.objects.filter(student=request.user, tenant=tenant, active=True).exists():
        raise PermissionDenied("You are not enrolled in an active batch.")
    if request.user.role == "teacher" and not TeacherSubjectAssignment.objects.filter(
            tenant=tenant, teacher=request.user, subject=question.topic.chapter.subject).exists():
        raise PermissionDenied("You are not assigned to this question's subject.")
    if figure_index < 0 or figure_index >= len(question.figure_paths):
        raise Http404("Question figure not found.")
    path = question.figure_paths[figure_index]
    if not default_storage.exists(path):
        raise Http404("Question figure not found.")
    return FileResponse(default_storage.open(path, "rb"), content_type=mimetypes.guess_type(path)[0] or "image/png")


@login_required
@require_http_methods(["GET", "POST"])
def assign_teacher_subject(request):
    tenant = _tenant_user(request, roles=("tenant_admin",))
    if not is_enabled(tenant, "teacher_assignments"):
        raise PermissionDenied("Teacher assignments are disabled for this institute.")
    form = TeacherAssignmentForm(request.POST or None, tenant=tenant)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if data["batch"] and data["batch"].tenant_id != tenant.pk:
            raise PermissionDenied("Batch belongs to another institute.")
        TeacherSubjectAssignment.objects.get_or_create(tenant=tenant, teacher=data["teacher"], subject=data["subject"], batch=data["batch"])
        AuditEvent.objects.create(tenant=tenant, actor=request.user, action="teacher_subject_assigned",
                                  object_type="TeacherSubjectAssignment", after={"teacher_id": data["teacher"].pk,
                                  "subject_id": data["subject"].pk, "batch_id": data["batch"].pk if data["batch"] else None})
        messages.success(request, "Teacher assignment saved.")
        return redirect("assign_teacher_subject")
    assignments = TeacherSubjectAssignment.objects.filter(tenant=tenant).select_related("teacher", "subject", "batch")
    return render(request, "learning/teacher_assignments.html", {"form": form, "assignments": assignments})
