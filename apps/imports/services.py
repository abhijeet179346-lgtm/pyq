import csv
import io
import json

from apps.catalog.models import Topic, Subtopic
from apps.content.models import Question

REQUIRED_COLUMNS = {"question_type", "stem", "options", "answer", "solution", "difficulty", "subject", "chapter", "topic", "source", "license"}
ALLOWED_TYPES = {choice for choice, _ in Question.Type.choices}
MAX_ROWS = 500


def parse_and_validate_csv(file_bytes, tenant, allowed_subject_ids=None):
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], ["File must use UTF-8 encoding."]
    reader = csv.DictReader(io.StringIO(text))
    headers = set(reader.fieldnames or [])
    missing = sorted(REQUIRED_COLUMNS - headers)
    if missing:
        return [], [f"Missing required columns: {', '.join(missing)}"]

    rows, errors = [], []
    for line_number, raw in enumerate(reader, start=2):
        if len(rows) >= MAX_ROWS:
            errors.append(f"Row {line_number}: maximum of {MAX_ROWS} data rows exceeded.")
            break
        row = {key: (value or "").strip() for key, value in raw.items() if key}
        row_errors = []
        for name in ("stem", "answer", "source", "license", "subject", "chapter", "topic"):
            if not row.get(name): row_errors.append(f"{name} is required")
        if row.get("question_type") not in ALLOWED_TYPES:
            row_errors.append("question_type must be one of: " + ", ".join(sorted(ALLOWED_TYPES)))
        try:
            difficulty = int(row.get("difficulty", ""))
            if not 1 <= difficulty <= 5: raise ValueError()
        except ValueError:
            difficulty = None
            row_errors.append("difficulty must be an integer from 1 to 5")
        try:
            options = json.loads(row.get("options") or "[]")
            if not isinstance(options, list): raise ValueError()
        except (json.JSONDecodeError, ValueError):
            options = []
            row_errors.append("options must be a JSON array (use [] for non-choice questions)")

        topic = None
        if not row_errors:
            topic = Topic.objects.filter(chapter__subject__name__iexact=row["subject"], chapter__name__iexact=row["chapter"], name__iexact=row["topic"]).first()
            if topic is None:
                row_errors.append("subject/chapter/topic does not match the catalog")
            elif allowed_subject_ids is not None and topic.chapter.subject_id not in allowed_subject_ids:
                row_errors.append("teacher is not assigned to this subject")
        subtopic = None
        if topic and row.get("subtopic"):
            subtopic = Subtopic.objects.filter(topic=topic, name__iexact=row["subtopic"]).first()
            if subtopic is None:
                row_errors.append("subtopic does not belong to the selected topic in the catalog")
        row["_subtopic_id"] = subtopic.pk if subtopic else None
        row["_line"] = line_number
        row["_options"] = options
        row["_difficulty"] = difficulty
        row["_topic_id"] = topic.pk if topic else None
        row["_errors"] = row_errors
        rows.append(row)
        errors.extend([f"Row {line_number}: {message}" for message in row_errors])
    if not rows:
        errors.append("The CSV contains no question rows.")
    return rows, errors


def commit_questions(rows, tenant, user):
    """All-or-nothing at the service boundary: callers must only commit clean previews."""
    if any(row.get("_errors") for row in rows):
        raise ValueError("Rows with validation errors cannot be committed.")
    created = []
    for row in rows:
        question = Question(
            kind=Question.Kind.PRACTICE, tenant=tenant, question_type=row["question_type"],
            stem=row["stem"], options=row["_options"], answer=row["answer"],
            solution=row.get("solution", ""), difficulty=row["_difficulty"],
            topic_id=row["_topic_id"], subtopic_id=row.get("_subtopic_id"), source=row["source"], license=row["license"],
            status=Question.Status.DRAFT, created_by=user,
        )
        question.full_clean()
        created.append(question)
    return Question.objects.bulk_create(created)


def to_preview_rows(rows):
    return [{"line": row["_line"], "stem": row.get("stem", "")[:120], "subject": row.get("subject", ""),
             "chapter": row.get("chapter", ""), "topic": row.get("topic", ""), "subtopic": row.get("subtopic", ""), "errors": row.get("_errors", [])}
            for row in rows]
