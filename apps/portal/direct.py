import json
import time
import uuid
from types import SimpleNamespace

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, F
from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse
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
from .models import DirectPracticeAttempt, PracticeBookmark, PracticeSession
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


PRACTICE_KEYS = {"exam", "subject", "chapter", "topic", "subtopic", "chapters", "topics", "subtopics", "year", "q", "difficulty", "question_type"}


def _new_session(request, tenant, ids, title, mode="learn", browse_url=""):
    session = PracticeSession.objects.create(user=request.user, tenant=tenant, title=title[:200], question_ids=list(ids),
        mode=mode if mode in PracticeSession.Mode.values else "learn", browse_url=browse_url[:600])
    return redirect("practice_session", session_id=session.pk, position=1)


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
        if key not in PRACTICE_KEYS:
            params.pop(key)
    params["view"] = "browse"
    browse_url = reverse("direct_bank") + "?" + params.urlencode()
    if request.POST.get("action") == "browse" or not queryset.exists():
        return redirect(browse_url + "#questions")
    order = request.POST.get("order", "sequential")
    if order == "random":
        ids = list(queryset.order_by("?").values_list("pk", flat=True)[:500])
    elif order == "newest":
        ids = list(queryset.order_by("-paper__year", "pk").values_list("pk", flat=True)[:500])
    else:
        ids = list(queryset.order_by("pk").values_list("pk", flat=True)[:500])
    count = request.POST.get("count", "20")
    if count.isdecimal() and int(count) > 0:
        ids = ids[:int(count)]
    title = exam.name + (" · " + context["selected_subject"] if context["selected_subject"] else " · Whole exam bank")
    return _new_session(request, tenant, ids, title, request.POST.get("mode", "learn"), browse_url)


def _practice_run(request, tenant, session_id):
    return get_object_or_404(PracticeSession, pk=session_id, user=request.user, tenant=tenant)


def _run_attempts(request, tenant, run):
    """Position → attempt for the answers recorded in this session (current question versions only)."""
    keys = {key: int(position) for position, key in run.answers.items()}
    attempts = DirectPracticeAttempt.objects.filter(user=request.user, submission_key__in=keys.keys(),
        question__in=questions(tenant), question_version=F("question__version")).select_related("question__topic__chapter")
    return {keys[str(attempt.submission_key)]: attempt for attempt in attempts}


def _palette(run, attempts):
    revealed = {int(p) for p in run.revealed}
    chips = []
    for position in range(1, len(run.question_ids) + 1):
        attempt = attempts.get(position)
        if attempt is None:
            state = "revealed" if position in revealed else "open"
        elif run.mode == "quiz":
            state = "saved"
        else:
            state = "correct" if attempt.is_correct else "wrong"
        chips.append({"position": position, "state": state})
    return chips


@login_required
@require_http_methods(["GET", "POST"])
def practice_session(request, session_id, position):
    tenant = _direct_user(request, "practice")
    run = _practice_run(request, tenant, session_id)
    if not 1 <= position <= len(run.question_ids):
        raise Http404()
    item = get_object_or_404(questions(tenant), pk=run.question_ids[position - 1])
    return _question_response(request, tenant, item, run=run, position=position)


@login_required
@require_GET
def practice_summary(request, session_id):
    tenant = _direct_user(request, "practice")
    run = _practice_run(request, tenant, session_id)
    attempts = _run_attempts(request, tenant, run)
    total = len(run.question_ids)
    correct = sum(1 for attempt in attempts.values() if attempt.is_correct)
    chapters = {}
    for attempt in attempts.values():
        name = attempt.question.topic.chapter.name if attempt.question.topic_id else "Other"
        row = chapters.setdefault(name, {"name": name, "total": 0, "correct": 0})
        row["total"] += 1
        row["correct"] += int(attempt.is_correct)
    for row in chapters.values():
        row["accuracy"] = round(100 * row["correct"] / row["total"])
    by_id = {q.pk: q for q in questions(tenant).filter(pk__in=run.question_ids)}
    review = []
    for position, question_id in enumerate(run.question_ids, 1):
        attempt = attempts.get(position)
        item = by_id.get(question_id)
        if item is None:
            continue
        review.append({"position": position, "question": item, "attempt": attempt, "revealed": str(position) in [str(p) for p in run.revealed],
                       "answer_display": answer_display(item)})
    seconds = [a.time_taken_seconds for a in attempts.values() if a.time_taken_seconds]
    return render(request, "portal/practice_summary.html", {"run": run, "session_id": run.pk,
        "total": total, "answered": len(attempts), "correct": correct, "wrong": len(attempts) - correct,
        "skipped": total - len(attempts), "accuracy": round(100 * correct / len(attempts)) if attempts else None,
        "avg_seconds": round(sum(seconds) / len(seconds)) if seconds else None,
        "chapters": sorted(chapters.values(), key=lambda r: r["accuracy"]), "review": review,
        "wrong_count": sum(1 for r in review if r["attempt"] and not r["attempt"].is_correct),
        "retry_count": sum(1 for r in review if not (r["attempt"] and r["attempt"].is_correct))})


@login_required
@require_http_methods(["POST"])
def retry_session(request, session_id):
    tenant = _direct_user(request, "practice")
    run = _practice_run(request, tenant, session_id)
    attempts = _run_attempts(request, tenant, run)
    include_skipped = request.POST.get("include") == "skipped"
    ids = [qid for position, qid in enumerate(run.question_ids, 1)
           if (position in attempts and not attempts[position].is_correct) or (include_skipped and position not in attempts)]
    if not ids:
        return redirect("practice_summary", session_id=run.pk)
    return _new_session(request, tenant, ids, "Retry · " + run.title.removeprefix("Retry · "), run.mode, run.browse_url)


@login_required
@require_http_methods(["POST"])
def start_bookmarks(request):
    tenant = _direct_user(request, "practice")
    ids = list(PracticeBookmark.objects.filter(user=request.user, question__in=questions(tenant)).values_list("question_id", flat=True))
    if not ids:
        return redirect("direct_progress")
    return _new_session(request, tenant, ids, "Bookmarked questions", request.POST.get("mode", "learn"))


@login_required
@require_http_methods(["POST"])
def toggle_bookmark(request, question_id):
    tenant = _direct_user(request, "practice")
    item = get_object_or_404(questions(tenant), pk=question_id)
    bookmark, created = PracticeBookmark.objects.get_or_create(user=request.user, question=item)
    if not created:
        bookmark.delete()
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"bookmarked": created})
    next_url = str(request.POST.get("next", ""))
    if not next_url.startswith("/practice/") or next_url.startswith("//"):
        next_url = reverse("direct_question", args=[item.pk])
    return redirect(next_url)


@login_required
@require_http_methods(["GET", "POST"])
def question(request, question_id):
    tenant = _direct_user(request, "practice")
    item = get_object_or_404(questions(tenant), pk=question_id)
    return _question_response(request, tenant, item)


def expected_answers(item):
    try:
        decoded = json.loads(item.answer)
        values = decoded if isinstance(decoded, list) else [decoded]
    except (ValueError, TypeError):
        values = [item.answer]
    return [str(value) for value in values]


def correct_option_ids(item):
    from apps.learning.answering import normalize_answer
    expected = {normalize_answer(value) for value in expected_answers(item)}
    return [key for key, text in option_pairs(item) if normalize_answer(key) in expected or normalize_answer(text) in expected]


def answer_display(item):
    ids = correct_option_ids(item)
    return ", ".join(ids) if ids else ", ".join(expected_answers(item))


def _feedback(tenant, item, attempt, run=None):
    from apps.materials.recommendations import recommend_sections
    recommendations = []
    if not attempt.is_correct:
        recommendations = [{"title": entry["section"].title, "url": reverse("direct_study_section", args=[entry["section"].pk])}
            for entry in recommend_sections(tenant, item.topic_id, item.topic.chapter.name, item.topic.chapter.subject.name, item.stem, item.pk)[:3]]
    if run is not None and run.mode == "quiz":
        # Quiz answers stay secret until the summary: no key, solution or correctness in the response.
        return {"state": "saved", "selected": attempt.selected_answers, "answer_ids": [], "answer": "", "solution": "", "recommendations": []}
    return {"state": "correct" if attempt.is_correct else "wrong",
            "selected": attempt.selected_answers, "answer_ids": correct_option_ids(item), "answer": answer_display(item),
            "solution": item.solution, "recommendations": recommendations}


def _question_response(request, tenant, item, run=None, position=None):
    is_fetch = request.headers.get("X-Requested-With") == "fetch"
    attempts = _run_attempts(request, tenant, run) if run else {}
    attempt = attempts.get(position) if run else None
    if run and str(position) in run.answers and attempt is None:
        run.answers.pop(str(position), None)
        run.save(update_fields=["answers", "updated_at"])
    if request.method == "POST" and request.POST.get("action") == "reveal":
        if run is not None and run.mode == "quiz":
            raise PermissionDenied("Solutions are shown at the end of a quiz.")
        if run and attempt is None and str(position) not in [str(p) for p in run.revealed]:
            run.revealed.append(position)
            run.save(update_fields=["revealed", "updated_at"])
        payload = {"state": "revealed", "answer_ids": correct_option_ids(item), "answer": answer_display(item), "solution": item.solution}
        return JsonResponse(payload) if is_fetch else redirect(request.path + "?revealed=1")
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
            if is_fetch:
                return JsonResponse({"error": "Choose or enter an answer first."}, status=400)
            return HttpResponseBadRequest("Choose or enter an answer first.")
        attempt, _ = DirectPracticeAttempt.objects.get_or_create(submission_key=key, defaults={
            "user": request.user, "question": item, "question_version": item.version,
            "selected_answers": selected, "is_correct": is_correct(item, selected),
            "time_taken_seconds": max(0, min(7200, int(time.time() - payload["started"]))),
        })
        if run is not None:
            run.answers[str(position)] = str(attempt.submission_key)
            run.save(update_fields=["answers", "updated_at"])
            attempts[position] = attempt
    if is_fetch and request.method == "POST":
        data = _feedback(tenant, item, attempt, run)
        if run is not None:
            data["palette"] = _palette(run, attempts)
            data["answered"] = len(attempts)
            if run.mode != "quiz":
                data["correct"] = sum(1 for a in attempts.values() if a.is_correct)
        return JsonResponse(data)
    token = signing.dumps({"user": request.user.pk, "question": item.pk,
        "tenant": tenant.pk, "version": item.version, "key": str(uuid.uuid4()), "started": time.time()}, salt="direct-practice")
    feedback = None
    if attempt is not None:
        feedback = _feedback(tenant, item, attempt, run)
    elif request.GET.get("revealed") or (run and str(position) in [str(p) for p in run.revealed]):
        feedback = {"state": "revealed", "answer_ids": correct_option_ids(item), "answer": answer_display(item), "solution": item.solution}
    show_answers = feedback is not None and feedback["state"] != "saved"
    selected = set(feedback["selected"]) if feedback and feedback.get("selected") else set()
    option_rows = [{"id": key, "text": text, "is_answer": show_answers and key in feedback["answer_ids"], "is_selected": key in selected}
                   for key, text in option_pairs(item)]
    context = {"question": item, "options": option_pairs(item), "option_rows": option_rows, "attempt": attempt,
        "feedback": feedback, "submission": token, "figure_urls": figure_urls(item),
        "bookmarked": PracticeBookmark.objects.filter(user=request.user, question=item).exists(),
        "practice_url": reverse("practice_session", args=[run.pk, position]) if run else reverse("direct_question", args=[item.pk])}
    if run:
        context.update({"run": run, "session_id": run.pk, "position": position, "total": len(run.question_ids),
            "next_position": position + 1 if position < len(run.question_ids) else None,
            "previous_position": position - 1 if position > 1 else None, "answered_count": len(attempts),
            "correct_count": sum(1 for a in attempts.values() if a.is_correct), "palette": _palette(run, attempts)})
        return render(request, "portal/practice_session.html", context)
    return render(request, "portal/direct_question.html", context)


@login_required
@require_GET
def progress(request):
    tenant = _direct_user(request, "mastery_analytics")
    attempts = DirectPracticeAttempt.objects.filter(user=request.user, question__in=questions(tenant))
    chapters = attempts.values("question__topic__chapter__name").annotate(
        total=Count("id"), correct=Count("id", filter=Q(is_correct=True))).order_by("question__topic__chapter__name")
    visible = questions(tenant)
    return render(request, "portal/direct_progress.html", {
        "chapters": chapters, "attempts": attempts.select_related("question__topic")[:50],
        "bookmarks": PracticeBookmark.objects.filter(user=request.user, question__in=visible).select_related("question__topic__chapter__subject")[:50],
        "sessions": PracticeSession.objects.filter(user=request.user, tenant=tenant)[:8]})


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
