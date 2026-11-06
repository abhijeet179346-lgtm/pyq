"""Build a learner's personal timed test from a practice selection."""
import math

from django.core.exceptions import ValidationError

from .models import TimedTest
from .timed_services import validate_questions

# New exam marking follows their 2024 information brochures. Custom tests are
# practice sets, so they do not reproduce paper section timing or question mix.
# https://comedk.org/uploads/comedk-2024-Exam-Brochure-version-1-1-released-on-27-Feb-2024.pdf
# https://cetcell.mahacet.org/wp-content/uploads/2023/12/Final_IB_MHT-CET-2024.pdf
PATTERNS = {
    "jee-main": {"rate": 2.4, "single_correct": (4, -1), "multi_correct": (4, -2), "integer": (4, -1), "numerical": (4, -1)},
    "neet": {"rate": 1.1, "single_correct": (4, -1), "multi_correct": (4, -1), "integer": (4, -1), "numerical": (4, -1)},
    "jee-advanced": {"rate": 3.3, "single_correct": (3, -1), "multi_correct": (4, -2), "integer": (3, 0), "numerical": (3, 0)},
    "comedk": {"rate": 1.0, "single_correct": (1, 0), "multi_correct": (1, 0), "integer": (1, 0), "numerical": (1, 0)},
    "mht-cet": {"rate": 1.2, "single_correct": (1, 0), "multi_correct": (1, 0), "integer": (1, 0), "numerical": (1, 0)},
    "cuet-ug": {"rate": 1.5, "single_correct": (5, -1), "multi_correct": (5, -1), "integer": (5, -1), "numerical": (5, -1)},
}
DEFAULT = {"rate": 2.0, "single_correct": (4, -1), "multi_correct": (4, -2), "integer": (4, 0), "numerical": (4, 0)}
MAX_QUESTIONS = 90
MAX_CUSTOM_TESTS = 50


def snapshot_row(question, pattern):
    from apps.learning.answering import option_pairs
    from apps.portal.direct import correct_option_ids, expected_answers
    kind = question.question_type
    options = [{"id": key, "text": text} for key, text in option_pairs(question)]
    if kind in ("single_correct", "multi_correct"):
        ids = correct_option_ids(question)
        answer = ids if kind == "multi_correct" else (ids[0] if len(ids) == 1 else ids)
    else:
        values = expected_answers(question)
        answer = values[0] if len(values) == 1 else values
    correct, incorrect = pattern.get(kind, DEFAULT[kind])
    topic = question.topic
    if question.paper.exam.slug == 'mht-cet' and topic.chapter.subject.name == 'Mathematics':
        correct = 2
    return {"key": f"q{question.pk}", "question_id": question.pk, "stem": question.stem, "question_type": kind,
            "subject": topic.chapter.subject.name, "chapter": topic.chapter.name, "topic": topic.name, "topic_id": topic.pk,
            "options": options, "answer": answer, "solution": question.solution, "source": question.source,
            "marking": {"correct": correct, "incorrect": incorrect, "unanswered": 0, "partial": kind == "multi_correct" and incorrect < -1},
            "figures": question.figure_paths or []}


def build_custom_test(user, tenant, exam, questions, title, minutes=None):
    """Return (test, skipped). Questions whose answer key can't be graded automatically are skipped."""
    if TimedTest.objects.filter(owner=user, tenant=tenant).count() >= MAX_CUSTOM_TESTS:
        raise ValueError(f"You already have {MAX_CUSTOM_TESTS} custom tests. Finish or reuse one before creating more.")
    pattern = PATTERNS.get(exam.slug, DEFAULT)
    rows, skipped = [], 0
    for question in questions[:MAX_QUESTIONS]:
        row = snapshot_row(question, pattern)
        try:
            validate_questions([row])
        except ValidationError:
            skipped += 1
            continue
        rows.append(row)
    if not rows:
        raise ValueError("None of the selected questions can be marked automatically. Choose different chapters.")
    if not minutes:
        minutes = min(180, max(5, math.ceil(len(rows) * pattern["rate"] / 5) * 5))
    from django.utils import timezone
    base = f"{title[:120]} · {timezone.localtime():%d %b %H:%M}"
    name, n = base, 2
    while TimedTest.objects.filter(tenant=tenant, name=name).exists():  # names are unique per tenant
        name, n = f"{base} ({n})", n + 1
    test = TimedTest(tenant=tenant, exam=exam, owner=user, series="Custom", subject="",
        name=name, duration_minutes=int(minutes), active=True,
        questions=rows, instructions=f"A custom test built from your practice selection: {len(rows)} questions in {minutes} minutes.")
    test.full_clean()
    test.save()
    return test, skipped
