"""Question-backed taxonomy, always scoped to the caller's visible question set."""
from django.db.models import Count
from django.http import Http404


def taxonomy_filters(visible, params):
    queryset = visible
    subject = params.get("subject", "").strip()
    selected = {key: params.get(key, "").strip() for key in ("chapter", "topic", "subtopic")}
    for value in selected.values():
        if value and not (value.isascii() and value.isdecimal() and len(value) <= 18):
            raise Http404("Invalid taxonomy selection.")
    selected = {key: str(int(value)) if value else "" for key, value in selected.items()}
    # Resolve a deep link upwards, while rejecting contradictory parent selections.
    for key, lookup in (("subtopic", "subtopic_id"), ("topic", "topic_id"), ("chapter", "topic__chapter_id")):
        if not selected[key]:
            continue
        item = visible.filter(**{lookup: int(selected[key])}).select_related("topic__chapter__subject", "subtopic").first()
        if item is None:
            raise Http404("This selection has no available questions in this exam.")
        expected = {"chapter": str(item.topic.chapter_id), "topic": str(item.topic_id), "subtopic": str(item.subtopic_id)}
        parents = ("chapter", "topic") if key == "subtopic" else ("chapter",) if key == "topic" else ()
        for parent in parents:
            if selected[parent] and int(selected[parent]) != int(expected[parent]):
                raise Http404("This selection belongs to a different parent.")
            selected[parent] = expected[parent]
        name = item.topic.chapter.subject.name
        if subject and subject not in (name, str(item.topic.chapter.subject_id)):
            raise Http404("This selection belongs to a different subject.")
        subject = name
    subjects = list(visible.order_by("topic__chapter__subject__name").values_list("topic__chapter__subject__name", flat=True).distinct())
    if subject:
        if subject.isascii() and subject.isdecimal() and len(subject) <= 18:
            row = visible.filter(topic__chapter__subject_id=int(subject)).first()
            if row is None:
                raise Http404("Subject unavailable.")
            subject = row.topic.chapter.subject.name
        if subject not in subjects:
            raise Http404("Subject unavailable.")
        queryset = queryset.filter(topic__chapter__subject__name=subject)
    chapters = list(queryset.exclude(topic__isnull=True).values("topic__chapter_id", "topic__chapter__name").annotate(total=Count("id")).order_by("topic__chapter__name"))
    if selected["chapter"]:
        queryset = queryset.filter(topic__chapter_id=int(selected["chapter"]))
    topics = list(queryset.exclude(topic__isnull=True).values("topic_id", "topic__name").annotate(total=Count("id")).order_by("topic__name"))
    if selected["topic"]:
        queryset = queryset.filter(topic_id=int(selected["topic"]))
    subtopics = list(queryset.exclude(subtopic__isnull=True).values("subtopic_id", "subtopic__name").annotate(total=Count("id")).order_by("subtopic__order", "subtopic__name"))
    if selected["subtopic"]:
        queryset = queryset.filter(subtopic_id=int(selected["subtopic"]))
    selected_chapter = next((row["topic__chapter__name"] for row in chapters if str(row["topic__chapter_id"]) == selected["chapter"]), "")
    selected_topic = next((row["topic__name"] for row in topics if str(row["topic_id"]) == selected["topic"]), "")
    selected_subtopic = next((row["subtopic__name"] for row in subtopics if str(row["subtopic_id"]) == selected["subtopic"]), "")
    return queryset, {"subjects": subjects, "chapters": chapters, "topics": topics, "subtopics": subtopics,
        "selected_subject": subject, "selected_chapter": selected["chapter"], "selected_topic": selected["topic"],
        "selected_subtopic": selected["subtopic"], "chapter_name": selected_chapter,
        "topic_name": selected_topic, "subtopic_name": selected_subtopic}
