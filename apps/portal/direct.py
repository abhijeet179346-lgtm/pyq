import time
import uuid
from types import SimpleNamespace

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, F
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods
from django.utils import timezone

from apps.features.models import TenantExam
from apps.tenancy.features import is_enabled
from apps.catalog.models import Exam
from apps.content.models import Question
from apps.learning.answering import is_correct, option_pairs
from apps.materials.models import MaterialSection
from .models import DirectPracticeAttempt
from .practice_selection import selection


def _direct_user(request, feature=None):
    if not getattr(request, "is_core_domain", False):
        raise Http404()
    if not request.user.is_authenticated or not request.user.email_verified or request.user.is_superuser:
        raise PermissionDenied("Sign in with a verified learner account.")
    tenant = getattr(request, "tenant", None)
    if tenant is None or not tenant.is_master or tenant.status != "active" or request.user.tenant_id != tenant.pk:
        raise PermissionDenied("Sign in with an active core-platform account.")
    if feature and not is_enabled(tenant, feature):
        raise PermissionDenied("This feature is disabled for the core platform.")
    return tenant


def questions(tenant):
    visible = Q(pk__in=[])
    if is_enabled(tenant, "practice"):
        if is_enabled(tenant, "global_pyq_bank"):
            exams = TenantExam.objects.filter(tenant=tenant, enabled=True, exam__active=True).values_list("exam_id", flat=True)
            visible |= Q(tenant__isnull=True, kind=Question.Kind.PYQ, paper__exam_id__in=exams)
        if is_enabled(tenant, "tenant_question_bank"):
            visible |= Q(tenant=tenant, kind=Question.Kind.PRACTICE)
    return Question.objects.filter(visible, status=Question.Status.PUBLISHED).select_related("topic__chapter__subject", "paper__exam", "subtopic")


def exam_cards(tenant):
    counts = dict(questions(tenant).values("paper__exam_id").annotate(total=Count("id")).values_list("paper__exam_id", "total"))
    exams = list(Exam.objects.filter(active=True, tenantexam__tenant=tenant, tenantexam__enabled=True))
    order = {"jee-main": 0, "neet": 1, "jee-advanced": 2}
    exams.sort(key=lambda exam: (order.get(exam.slug, 3), exam.name))
    descriptions = {
        "jee-main": ("Joint Entrance Examination (Main)", "Build speed and accuracy with shift-wise previous-year problems.", "Engineering"),
        "neet": ("National Eligibility cum Entrance Test", "Strengthen Biology, Physics and Chemistry for your medical entrance.", "Medical"),
        "jee-advanced": ("Joint Entrance Examination (Advanced)", "Take on conceptual, numerical and multiple-correct challenges.", "Engineering"),
    }
    for exam in exams:
        exam.question_count = counts.get(exam.pk, 0)
        exam.full_name, exam.description, exam.category = descriptions.get(exam.slug, (exam.name, "Explore the published questions in this vault.", "Exam"))
        years = list(questions(tenant).filter(paper__exam=exam).order_by("paper__year").values_list("paper__year", flat=True).distinct())
        exam.year_range = str(years[0]) if len(years) == 1 else f"{years[0]}–{years[-1]}" if years else "Coming soon"
    practice_count = questions(tenant).filter(kind=Question.Kind.PRACTICE).count()
    if practice_count:
        exams.append(SimpleNamespace(pk="practice", slug="practice", name="Additional practice", question_count=practice_count))
    return exams


def dashboard(request):
    tenant = _direct_user(request)
    attempts = DirectPracticeAttempt.objects.filter(user=request.user, question__in=questions(tenant))
    stats = attempts.aggregate(total=Count("id"), correct=Count("id", filter=Q(is_correct=True)))
    return render(request, "portal/direct_home.html", {
        "practice_count": stats["total"],
        "accuracy": round(stats["correct"] * 100 / stats["total"]) if stats["total"] else None,
        "question_count": questions(tenant).count(), "recent_attempts": attempts.select_related("question__topic")[:5],
        "exams": exam_cards(tenant),
        "today_attempts": attempts.filter(submitted_at__date=timezone.localdate()).count(),
    })


@login_required
@require_GET
def question_bank(request):
    tenant = _direct_user(request, "practice")
    exams = exam_cards(tenant)
    exam_id = request.GET.get("exam", "")
    if not exam_id:
        return render(request, "portal/direct_bank.html", {"exams": exams})
    selected = next((exam for exam in exams if str(exam.pk) == exam_id), None)
    if selected is None:
        raise Http404("This exam is not available.")
    queryset = questions(tenant)
    queryset = queryset.filter(kind=Question.Kind.PRACTICE) if selected.pk == "practice" else queryset.filter(paper__exam=selected)
    years = queryset.exclude(paper__isnull=True).values_list("paper__year", flat=True).distinct().order_by("-paper__year")
    subject_cards = list(queryset.values("topic__chapter__subject__name").annotate(total=Count("id")).order_by("topic__chapter__subject__name"))
    queryset, context = selection(queryset, request.GET)
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "portal/direct_bank.html", {
        "page": Paginator(queryset, 20).get_page(request.GET.get("page")) if request.GET.get("view") == "browse" else None,
        "question_count": queryset.count(), "subject_cards": subject_cards,
        "is_browsing": request.GET.get("view") == "browse",
        "exams": exams, "selected_exam": selected, "years": years,
        **context, "filter_query": params.urlencode(),
    })


@login_required
@require_http_methods(["POST"])
def start_practice(request):
    tenant = _direct_user(request, "practice")
    exam_id = request.POST.get("exam", "")
    exam = next((item for item in exam_cards(tenant) if str(item.pk) == exam_id), None)
    if exam is None:
        raise Http404("Exam unavailable.")
    visible = questions(tenant)
    visible = visible.filter(kind=Question.Kind.PRACTICE) if exam.pk == "practice" else visible.filter(paper__exam=exam)
    queryset, context = selection(visible, request.POST)
    params = request.POST.copy()
    for key in list(params):
        if key not in {"exam", "subject", "chapter", "topic", "subtopic", "chapters", "topics", "subtopics", "year", "q", "difficulty", "question_type"}:
            params.pop(key)
    params["view"] = "browse"
    browse_url = reverse("direct_bank") + "?" + params.urlencode()
    if request.POST.get("action") == "browse" or not queryset.exists():
        return redirect(browse_url + "#questions")
    key = str(uuid.uuid4())
    runs = request.session.get("practice_runs", {})
    runs[key] = {"user": request.user.pk, "tenant": tenant.pk, "questions": list(queryset.order_by("pk").values_list("pk", flat=True)),
        "answers": {}, "title": exam.name + (" · " + context["selected_subject"] if context["selected_subject"] else " · Whole exam bank"), "browse_url": browse_url}
    # Bound session storage; saved answer history lives in DirectPracticeAttempt.
    request.session["practice_runs"] = dict(list(runs.items())[-5:])
    return redirect("practice_session", session_id=key, position=1)


def _practice_run(request, tenant, session_id):
    run = request.session.get("practice_runs", {}).get(str(session_id))
    if not run or run["user"] != request.user.pk or run["tenant"] != tenant.pk:
        raise Http404("Practice session unavailable.")
    return run


@login_required
@require_http_methods(["GET", "POST"])
def practice_session(request, session_id, position):
    tenant = _direct_user(request, "practice")
    run = _practice_run(request, tenant, session_id)
    if not 1 <= position <= len(run["questions"]):
        raise Http404()
    item = get_object_or_404(questions(tenant), pk=run["questions"][position - 1])
    return _question_response(request, tenant, item, run=run, session_id=str(session_id), position=position)


@login_required
@require_GET
def practice_summary(request, session_id):
    tenant = _direct_user(request, "practice")
    run = _practice_run(request, tenant, session_id)
    attempts = DirectPracticeAttempt.objects.filter(user=request.user, question__in=questions(tenant), submission_key__in=run["answers"].values(), question_version=F("question__version"))
    answered = attempts.count()
    return render(request, "portal/practice_summary.html", {"run": run, "session_id": session_id,
        "total": len(run["questions"]), "answered": answered, "correct": attempts.filter(is_correct=True).count(),
        "skipped": len(run["questions"]) - answered})


@login_required
@require_http_methods(["GET", "POST"])
def question(request, question_id):
    tenant = _direct_user(request, "practice")
    item = get_object_or_404(questions(tenant), pk=question_id)
    return _question_response(request, tenant, item)


def _question_response(request, tenant, item, run=None, session_id=None, position=None):
    attempt = None
    if run and str(position) in run["answers"]:
        attempt = DirectPracticeAttempt.objects.filter(user=request.user, question=item, question_version=item.version, submission_key=run["answers"][str(position)]).first()
        if attempt is None:
            run["answers"].pop(str(position), None)
    if request.method == "POST" and attempt is None:
        try:
            payload = signing.loads(request.POST.get("submission", ""), salt="direct-practice", max_age=7200)
            if not isinstance(payload, dict):
                raise ValueError()
            key = uuid.UUID(payload["key"])
            if payload["tenant"] != tenant.pk or payload["user"] != request.user.pk or payload["question"] != item.pk or payload["version"] != item.version:
                raise ValueError()
        except (signing.BadSignature, ValueError, TypeError, KeyError):
            return HttpResponseBadRequest("This practice form expired or changed. Reload the question and try again.")
        selected = [value[:4000] for value in request.POST.getlist("answer")[:50]]
        if not selected or not any(value.strip() for value in selected):
            return HttpResponseBadRequest("Choose or enter an answer first.")
        attempt, _ = DirectPracticeAttempt.objects.get_or_create(submission_key=key, defaults={
            "user": request.user, "question": item, "question_version": item.version,
            "selected_answers": selected, "is_correct": is_correct(item, selected),
            "time_taken_seconds": max(0, min(7200, int(time.time() - payload["started"]))),
        })
    if run and attempt is not None:
        run["answers"][str(position)] = str(attempt.submission_key)
        runs = request.session.get("practice_runs", {})
        runs[session_id] = run
        request.session["practice_runs"] = runs
    run_context = {}
    if run:
        run_context = {"run": run, "session_id": session_id, "position": position, "total": len(run["questions"]),
            "next_position": position + 1 if position < len(run["questions"]) else None,
            "previous_position": position - 1 if position > 1 else None, "answered_count": len(run["answers"])}
    token = signing.dumps({"user": request.user.pk, "question": item.pk,
        "tenant": tenant.pk, "version": item.version, "key": str(uuid.uuid4()), "started": time.time()}, salt="direct-practice")
    from apps.materials.recommendations import recommend_sections
    recommendations=[]
    if attempt is not None and not attempt.is_correct:
        recommendations=recommend_sections(tenant,item.topic_id,item.topic.chapter.name,item.topic.chapter.subject.name,item.stem,item.pk)
    return render(request, "portal/direct_question.html", {"question": item, "recommendations":recommendations,
        "figure_urls": figure_urls(item),
        "options": option_pairs(item), "attempt": attempt, "submission": token, **run_context})


@login_required
@require_GET
def progress(request):
    tenant = _direct_user(request, "mastery_analytics")
    attempts = DirectPracticeAttempt.objects.filter(user=request.user, question__in=questions(tenant))
    chapters = attempts.values("question__topic__chapter__name").annotate(
        total=Count("id"), correct=Count("id", filter=Q(is_correct=True))).order_by("question__topic__chapter__name")
    return render(request, "portal/direct_progress.html", {
        "chapters": chapters, "attempts": attempts.select_related("question__topic")[:50]})


def _study_sections(tenant):
    enabled = TenantExam.objects.filter(tenant=tenant,enabled=True,exam__active=True).values_list("exam_id",flat=True)
    return MaterialSection.objects.filter(active=True,document__active=True).filter(
        Q(document__tenant=tenant)|Q(document__tenant__isnull=True)).filter(
        Q(document__exams__isnull=True)|Q(document__exams__in=enabled)).select_related(
        "document","topic__chapter__subject").prefetch_related("document__exams").distinct()


@login_required
@require_GET
def library(request):
    tenant = _direct_user(request, "study_material")
    sections = _study_sections(tenant)
    exams = Exam.objects.filter(active=True,tenantexam__tenant=tenant,tenantexam__enabled=True)
    exam = request.GET.get("exam", "")
    if exam:
        if not exam.isascii() or not exam.isdecimal() or len(exam)>18 or not exams.filter(pk=int(exam)).exists():
            raise Http404("Exam unavailable.")
        sections = sections.filter(Q(document__exams__pk=int(exam))|Q(document__exams__isnull=True)).distinct()
    subjects = list(sections.values_list("topic__chapter__subject__name",flat=True).order_by("topic__chapter__subject__name").distinct())
    subject = request.GET.get("subject", "")
    if subject:sections=sections.filter(topic__chapter__subject__name=subject)
    chapters = list(sections.values("topic__chapter_id","topic__chapter__name").order_by("topic__chapter__name").distinct())
    chapter = request.GET.get("chapter", "")
    if chapter:
        if not chapter.isascii() or not chapter.isdecimal() or len(chapter)>18:raise Http404("Invalid chapter.")
        sections=sections.filter(topic__chapter_id=int(chapter))
    query=request.GET.get("q", "").strip()
    if query:sections=sections.filter(Q(title__icontains=query)|Q(body__icontains=query)|Q(topic__name__icontains=query))
    params=request.GET.copy();params.pop("page",None)
    page=Paginator(sections.order_by("topic__chapter__subject__name","topic__chapter__name","title"),12).get_page(request.GET.get("page"))
    return render(request,"portal/direct_library.html",{"page":page,"exams":exams,"subjects":subjects,"chapters":chapters,
        "selected_exam":exam,"selected_subject":subject,"selected_chapter":chapter,"search":query,"filter_query":params.urlencode()})


def _material_reader(request):
    if getattr(request,"is_core_domain",False):return _direct_user(request,"study_material")
    from apps.learning.views import _tenant_user
    from .services import workspace_ready
    tenant=_tenant_user(request)
    if not workspace_ready(request.user,tenant) or not is_enabled(tenant,"study_material"):raise PermissionDenied("Study material access is disabled.")
    return tenant


@login_required
@require_GET
def study_section(request, section_id):
    tenant = _material_reader(request)
    section=get_object_or_404(_study_sections(tenant),pk=section_id)
    related=questions(tenant).filter(topic__chapter=section.topic.chapter)[:5]
    return render(request,"portal/study_section.html",{"section":section,"related_questions":related})


@login_required
@require_GET
def study_source(request, section_id):
    from django.http import FileResponse
    from pathlib import Path
    tenant=_material_reader(request)
    section=get_object_or_404(_study_sections(tenant),pk=section_id)
    file=section.document.source_file
    if not file or not file.storage.exists(file.name):raise Http404("Source file unavailable.")
    return FileResponse(file.open("rb"),as_attachment=not file.name.lower().endswith(".pdf"),filename=Path(file.name).name)


def figure_urls(question):
    return [reverse("direct_question_figure", args=[question.pk, index]) for index in range(len(question.figure_paths or []))]


@login_required
@require_GET
def question_figure(request, question_id, index):
    from apps.content.figures import content_type, open_figure
    tenant = _direct_user(request, "practice")
    item = get_object_or_404(questions(tenant), pk=question_id)
    if not 0 <= index < len(item.figure_paths or []):
        raise Http404()
    handle = open_figure(item.figure_paths[index])
    if handle is None:
        raise Http404()
    response = FileResponse(handle, content_type=content_type(item.figure_paths[index]))
    response["Cache-Control"] = "private, max-age=3600"
    return response
