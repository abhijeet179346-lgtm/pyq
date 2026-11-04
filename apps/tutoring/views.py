import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.assessments.models import AttemptAnswer, TestAttempt
from apps.audit.models import AuditEvent
from apps.catalog.models import Topic
from apps.learning.models import BatchEnrollment, PracticeAttempt, TeacherBatchAssignment, TeacherSubjectAssignment
from apps.learning.views import _tenant_user
from apps.tenancy.features import is_enabled
from .models import ConceptMastery, MistakeDiagnosis, SpacedReview, TutorMessage, TutorThread
from .services import tutor_chat, tutor_mistake
from .forms import TutorPhotoForm


def _ensure_not_in_test(user):
    if TestAttempt.objects.filter(student=user, status=TestAttempt.Status.IN_PROGRESS).exists():
        raise PermissionDenied("Tutor help is unavailable while a timed test attempt is in progress.")


def _check_daily_limit(tenant, user):
    ai_settings = getattr(tenant, "ai_settings", None)
    daily_limit = ai_settings.daily_message_limit if ai_settings else 50
    today_count = TutorMessage.objects.filter(thread__tenant=tenant, thread__student=user,
        role=TutorMessage.Role.STUDENT, created_at__date=timezone.localdate()).count()
    if today_count >= daily_limit:
        raise PermissionDenied("Daily tutor message limit reached. Ask your teacher for help.")


@login_required
@require_http_methods(["GET", "POST"])
def tutor_home(request):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "ai_tutor_chat"):
        raise PermissionDenied("Tutor chat is disabled for this institute.")
    _ensure_not_in_test(request.user)
    if request.method == "POST":
        topic = get_object_or_404(Topic, pk=request.POST.get("topic"))
        if request.user.role == "teacher" and not TeacherSubjectAssignment.objects.filter(
                tenant=tenant, teacher=request.user, subject=topic.chapter.subject).exists():
            raise PermissionDenied("You are not assigned to this subject.")
        prompt = request.POST.get("message", "").strip()[:4000]
        if not prompt:
            messages.error(request, "Enter a question for the tutor.")
            return redirect("tutor_home")
        _check_daily_limit(tenant, request.user)
        style = request.POST.get("style", "hint")
        if style not in {"hint", "explain", "simpler"}: style = "hint"
        language = request.POST.get("language", "en")
        if language not in {"en", "hi", "hinglish"}: language = "en"
        thread = TutorThread.objects.create(tenant=tenant, student=request.user, topic=topic,
            teaching_style=style, language=language)
        TutorMessage.objects.create(thread=thread, role=TutorMessage.Role.STUDENT, content=prompt)
        answer, citations = tutor_chat(tenant=tenant, student=request.user, thread=thread, prompt=prompt)
        TutorMessage.objects.create(thread=thread, role=TutorMessage.Role.TUTOR, content=answer, citations=citations)
        return redirect("tutor_thread", thread_id=thread.pk)
    topics = Topic.objects.order_by("chapter__subject__name", "chapter__name", "name")
    if request.user.role == "teacher":
        assigned_subjects = TeacherSubjectAssignment.objects.filter(tenant=tenant, teacher=request.user).values_list("subject_id", flat=True)
        topics = topics.filter(chapter__subject_id__in=assigned_subjects)
    threads = TutorThread.objects.filter(tenant=tenant, student=request.user).order_by("-updated_at")[:30]
    return render(request, "tutoring/home.html", {"topics": topics, "threads": threads})


@login_required
@require_http_methods(["GET", "POST"])
def tutor_photo(request):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "photo_question_input"):
        raise PermissionDenied("Photo question input is disabled for this institute.")
    _ensure_not_in_test(request.user)
    if request.method == "POST":
        _check_daily_limit(tenant, request.user)
    if request.method == "POST" and request.POST.get("stage") == "confirm":
        preview = request.session.pop("tutor_photo_preview", None)
        if not preview or str(preview.get("topic_id")) != request.POST.get("topic"):
            raise PermissionDenied("Photo transcription expired. Upload the image again.")
        topic = get_object_or_404(Topic, pk=request.POST.get("topic"))
        if request.user.role == "teacher" and not TeacherSubjectAssignment.objects.filter(
                tenant=tenant, teacher=request.user, subject=topic.chapter.subject).exists():
            raise PermissionDenied("You are not assigned to this subject.")
        transcription = request.POST.get("transcription", "").strip()[:4000]
        if not transcription:
            messages.error(request, "Confirm or correct the transcription first.")
            return redirect("tutor_photo")
        thread = TutorThread.objects.create(tenant=tenant, student=request.user, topic=topic)
        TutorMessage.objects.create(thread=thread, role=TutorMessage.Role.STUDENT, content=transcription)
        answer, citations = tutor_chat(tenant=tenant, student=request.user, thread=thread, prompt=transcription)
        TutorMessage.objects.create(thread=thread, role=TutorMessage.Role.TUTOR, content=answer, citations=citations)
        return redirect("tutor_thread", thread_id=thread.pk)
    form = TutorPhotoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        from apps.scans.services import _request_page
        topic = get_object_or_404(Topic.objects.select_related("chapter__subject"), pk=form.cleaned_data["topic"])
        if request.user.role == "teacher" and not TeacherSubjectAssignment.objects.filter(
                tenant=tenant, teacher=request.user, subject=topic.chapter.subject).exists():
            raise PermissionDenied("You are not assigned to this subject.")
        result = _request_page(form.cleaned_data["image"].read(), [{"id": topic.pk, "name": topic.name,
            "chapter": topic.chapter.name, "subject": topic.chapter.subject.name}], tenant=tenant,
            user=request.user, operation="photo_question")
        if not result.get("questions"):
            messages.error(request, "The image did not contain a readable question.")
        else:
            extracted = result["questions"][0]
            transcription = extracted.get("stem", "") + "\n\nOptions: " + json.dumps(extracted.get("options", []), ensure_ascii=False)
            request.session["tutor_photo_preview"] = {"topic_id": topic.pk, "transcription": transcription[:4000]}
            return render(request, "tutoring/photo_confirm.html", {"topic": topic, "transcription": transcription[:4000]})
    return render(request, "tutoring/photo.html", {"form": form, "topics": Topic.objects.order_by("name")})


@login_required
@require_http_methods(["GET", "POST"])
def tutor_thread(request, thread_id):
    tenant = _tenant_user(request)
    thread = get_object_or_404(TutorThread.objects.select_related("topic", "student"), pk=thread_id, tenant=tenant)
    student_owner = thread.student_id == request.user.pk
    teacher_owner = request.user.role == "teacher" and thread.subject_teacher_id == request.user.pk
    if not (student_owner or teacher_owner or request.user.role == "tenant_admin" or request.user.is_superuser):
        raise PermissionDenied("This tutor thread is private to its student and assigned teacher.")
    if student_owner:
        _ensure_not_in_test(request.user)
    if request.method == "POST":
        if student_owner:
            if not is_enabled(tenant, "ai_tutor_chat"):
                raise PermissionDenied("Tutor chat is disabled.")
            prompt = request.POST.get("message", "").strip()[:4000]
            if not prompt: return redirect("tutor_thread", thread_id=thread.pk)
            _check_daily_limit(tenant, request.user)
            TutorMessage.objects.create(thread=thread, role=TutorMessage.Role.STUDENT, content=prompt)
            answer, citations = tutor_chat(tenant=tenant, student=request.user, thread=thread, prompt=prompt)
            TutorMessage.objects.create(thread=thread, role=TutorMessage.Role.TUTOR, content=answer, citations=citations)
        elif teacher_owner:
            reply = request.POST.get("message", "").strip()[:6000]
            if reply:
                TutorMessage.objects.create(thread=thread, role=TutorMessage.Role.TEACHER, content=reply)
                if request.POST.get("save_material") == "1" and is_enabled(tenant, "material_import"):
                    from apps.materials.models import MaterialDocument, MaterialSection
                    document, _ = MaterialDocument.objects.get_or_create(tenant=tenant,
                        title=f"Teacher responses: {thread.topic}", source="Teacher-reviewed tutor response",
                        license="Institute-owned", uploaded_by=request.user)
                    section = MaterialSection.objects.create(document=document, title=f"Answer: {reply[:80]}",
                        body=reply, topic=thread.topic, order=document.sections.count())
                    AuditEvent.objects.create(tenant=tenant, actor=request.user, action="teacher_reply_saved_as_material",
                        object_type="MaterialSection", object_id=str(section.pk), after={"thread_id": thread.pk})
                thread.status = TutorThread.Status.OPEN
                thread.save(update_fields=["status", "updated_at"])
        else:
            raise PermissionDenied("Only the student or assigned teacher may reply.")
        return redirect("tutor_thread", thread_id=thread.pk)
    return render(request, "tutoring/thread.html", {"thread": thread, "messages": thread.messages.all(), "can_reply": student_owner or teacher_owner})


@login_required
@require_http_methods(["POST"])
def tutor_feedback(request, message_id):
    tenant = _tenant_user(request)
    message = get_object_or_404(TutorMessage, pk=message_id, thread__tenant=tenant,
        thread__student=request.user, role=TutorMessage.Role.TUTOR)
    value = request.POST.get("feedback")
    if value not in ("helpful", "wrong"):
        raise PermissionDenied("Invalid feedback value.")
    message.feedback = value
    message.save(update_fields=["feedback"])
    AuditEvent.objects.create(tenant=tenant, actor=request.user, action="tutor_answer_feedback",
        object_type="TutorMessage", object_id=str(message.pk), after={"feedback": value})
    if value == "wrong":
        message.thread.status = TutorThread.Status.ESCALATED
        if is_enabled(tenant, "teacher_escalation"):
            subject_id = message.thread.topic.chapter.subject_id if message.thread.topic_id else None
            assignment = TeacherSubjectAssignment.objects.filter(tenant=tenant, subject_id=subject_id).select_related("teacher").first()
            if assignment: message.thread.subject_teacher = assignment.teacher
        message.thread.save(update_fields=["status", "subject_teacher", "updated_at"])
    return redirect("tutor_thread", thread_id=message.thread_id)


@login_required
@require_http_methods(["POST"])
def escalate_thread(request, thread_id):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "teacher_escalation"):
        raise PermissionDenied("Teacher escalation is disabled for this institute.")
    thread = get_object_or_404(TutorThread, pk=thread_id, tenant=tenant, student=request.user)
    subject_id = thread.topic.chapter.subject_id if thread.topic_id else None
    assignment = TeacherSubjectAssignment.objects.filter(tenant=tenant, subject_id=subject_id).select_related("teacher").first()
    if assignment is None:
        messages.error(request, "No teacher is assigned to this subject yet.")
    else:
        thread.subject_teacher = assignment.teacher
        thread.status = TutorThread.Status.ESCALATED
        thread.save(update_fields=["subject_teacher", "status", "updated_at"])
        AuditEvent.objects.create(tenant=tenant, actor=request.user, action="tutor_thread_escalated",
            object_type="TutorThread", object_id=str(thread.pk), after={"teacher_id": assignment.teacher_id})
        messages.success(request, "Your teacher has been notified in the institute workspace.")
    return redirect("tutor_thread", thread_id=thread.pk)


@login_required
@require_http_methods(["GET"])
def mistake_help(request, source, attempt_id):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "ai_tutor_mistake"):
        raise PermissionDenied("Mistake tutor is disabled for this institute.")
    _ensure_not_in_test(request.user)
    _check_daily_limit(tenant, request.user)
    practice = test_answer = None
    if source == "practice":
        practice = get_object_or_404(PracticeAttempt.objects.select_related("question__topic"),
            pk=attempt_id, tenant=tenant, student=request.user, is_correct=False)
        question, selected = practice.question, practice.selected_answer
    elif source == "test":
        test_answer = get_object_or_404(AttemptAnswer.objects.select_related("question__topic", "attempt"),
            pk=attempt_id, attempt__tenant=tenant, attempt__student=request.user, is_correct=False)
        question, selected = test_answer.question, test_answer.answer
    else:
        raise PermissionDenied("Unknown mistake source.")
    diagnosis = tutor_mistake(tenant=tenant, student=request.user, question=question,
        selected_answer=selected, practice_attempt=practice, test_answer=test_answer)
    thread = TutorThread.objects.create(tenant=tenant, student=request.user, topic=question.topic, question=question)
    TutorMessage.objects.create(thread=thread, role=TutorMessage.Role.STUDENT, content=f"I answered: {selected}")
    TutorMessage.objects.create(thread=thread, role=TutorMessage.Role.TUTOR,
        content=diagnosis.diagnosis + "\n\n" + diagnosis.explanation, citations=diagnosis.citations)
    return render(request, "tutoring/mistake.html", {"question": question, "selected": selected,
        "diagnosis": diagnosis, "thread": thread})


@login_required
@require_http_methods(["GET"])
def mastery_home(request):
    tenant = _tenant_user(request)
    if not is_enabled(tenant, "mastery_analytics"):
        raise PermissionDenied("Mastery analytics are disabled for this institute.")
    rows = ConceptMastery.objects.filter(tenant=tenant, student=request.user).select_related("concept_tag__topic__chapter__subject")
    due_reviews = SpacedReview.objects.filter(tenant=tenant, student=request.user, completed_at__isnull=True,
        due_at__lte=timezone.now()).select_related("question")[:20]
    return render(request, "tutoring/mastery.html", {"rows": rows, "due_reviews": due_reviews})


@login_required
@require_http_methods(["GET"])
def teacher_tutor_analytics(request, batch_id):
    tenant = _tenant_user(request, roles=("tenant_admin", "teacher") if not request.user.is_superuser else None)
    if not is_enabled(tenant, "mastery_analytics"):
        raise PermissionDenied("Tutor analytics are disabled.")
    from apps.learning.models import Batch
    batch = get_object_or_404(Batch, pk=batch_id, tenant=tenant, active=True)
    if request.user.role == "teacher" and not TeacherBatchAssignment.objects.filter(teacher=request.user, batch=batch).exists():
        raise PermissionDenied("This batch is not assigned to you.")
    student_ids = batch.enrollments.filter(active=True).values_list("student_id", flat=True)
    weak = ConceptMastery.objects.filter(tenant=tenant, student_id__in=student_ids).values(
        "concept_tag__name", "concept_tag__topic__name", "concept_tag__topic__chapter__subject__name").annotate(
        average=Avg("score"), count=Count("id")).order_by("average")[:20]
    doubts = TutorThread.objects.filter(tenant=tenant, student_id__in=student_ids).values(
        "topic__chapter__subject__name", "topic__chapter__name", "topic__name").annotate(count=Count("id")).order_by("-count")[:20]
    queue = TutorThread.objects.filter(tenant=tenant, student_id__in=student_ids, status=TutorThread.Status.ESCALATED)
    wrong_answers = TutorMessage.objects.filter(thread__tenant=tenant, thread__student_id__in=student_ids,
        role=TutorMessage.Role.TUTOR, feedback="wrong").select_related("thread", "thread__topic")[:50]
    return render(request, "tutoring/teacher_analytics.html", {"batch": batch, "weak": weak, "doubts": doubts,
        "queue": queue, "wrong_answers": wrong_answers})
