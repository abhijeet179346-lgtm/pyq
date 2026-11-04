"""Local question candidates from Docling's page Markdown for the scan review queue.

Uses the same parser as admin PDF-to-Excel jobs (apps.imports.pdf_excel) and
converts its rows to the scan payload shape. This is a review aid: no answer is
inferred unless an explicit printed marker is present.
"""

import json

from apps.imports.pdf_excel import LOCAL_OCR_WARNING, split_questions


class _Source:
    pk, source, license = "scan", "", ""


def _topic_from_context(text, filename, topics):
    heading = " ".join(text.splitlines()[:4]).lower()
    context = heading + " " + filename.lower()
    matches = [topic for topic in topics if len(topic["name"]) >= 5 and topic["name"].lower() in context]
    return max(matches, key=lambda item: len(item["name"]), default=None)


def extract_local_questions(markdown, topics, filename=""):
    topic = _topic_from_context(markdown, filename, topics)
    rows = []
    for parsed in split_questions(markdown, 0, _Source, LOCAL_OCR_WARNING):
        options = [{"id": letter, "text": parsed["option_" + letter.lower()]}
                   for letter in "ABCD" if parsed["option_" + letter.lower()]]
        answer = parsed["answer"]
        if parsed["question_type"] == "multi_correct" and answer:
            answer = json.dumps(answer.split(","))
        rows.append({
            "stem": parsed["stem"], "options": options, "answer": answer, "solution": parsed["solution"],
            "question_type": parsed["question_type"], "difficulty": 3,
            "topic_id": topic["id"] if topic else "", "needs_topic_review": topic is None,
            "tags": [topic["subject"], topic["chapter"], topic["name"]] if topic else [],
            "raw_text": parsed["original_text"], "warnings": parsed["warnings"],
        })
    return rows
