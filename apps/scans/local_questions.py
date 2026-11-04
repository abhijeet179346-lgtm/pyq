"""Conservative local question candidates from Docling's page Markdown.

This is a review aid: numbered worked examples or answer keys may also match.
No answer is inferred unless an explicit printed marker is present.
"""

import re


QUESTION_START = re.compile(r"(?m)^\s*(\d{1,3})[.)]\s+(?=[A-Za-z(])")
OPTION_START = re.compile(r"(?m)^\s*[-•]?\s*\(?([A-Da-d1-4])\)\s+")
ANSWER_LINE = re.compile(r"(?im)^\s*(?:ans(?:wer)?\.?|exp\.?)\s*[:.]?\s*\(?([A-Da-d1-4])\)?\s*$")


def _topic_from_context(text, filename, topics):
    heading = " ".join(text.splitlines()[:4]).lower()
    context = heading + " " + filename.lower()
    matches = [topic for topic in topics if len(topic["name"]) >= 5 and topic["name"].lower() in context]
    return max(matches, key=lambda item: len(item["name"]), default=None)


def extract_local_questions(markdown, topics, filename=""):
    starts = list(QUESTION_START.finditer(markdown))
    topic = _topic_from_context(markdown, filename, topics)
    rows = []
    for index, match in enumerate(starts):
        block = markdown[match.end():starts[index + 1].start() if index + 1 < len(starts) else len(markdown)].strip()
        if len(block) < 30:
            continue
        option_matches = list(OPTION_START.finditer(block))
        if option_matches:
            stem = block[:option_matches[0].start()].strip()
        else:
            stem = block
        options = []
        for position, option in enumerate(option_matches):
            option_text = block[option.end():option_matches[position + 1].start()
                                if position + 1 < len(option_matches) else len(block)].strip()
            marker = ANSWER_LINE.search(option_text)
            if marker:
                option_text = option_text[:marker.start()].strip()
            options.append({"id": option.group(1).upper(), "text": option_text})
        printed_answer = ANSWER_LINE.search(block)
        answer = printed_answer.group(1).upper() if printed_answer else ""
        solution = block[printed_answer.end():].strip() if printed_answer else ""
        if printed_answer:
            stem = stem[:printed_answer.start()].strip() if not option_matches else stem
        tags = ([topic["subject"], topic["chapter"], topic["name"]] if topic else [])
        rows.append({
            "stem": stem, "options": options, "answer": answer, "solution": solution,
            "question_type": "single_correct" if options else "numerical", "difficulty": 3,
            "topic_id": topic["id"] if topic else "", "needs_topic_review": topic is None,
            "tags": tags, "raw_text": block,
            "warnings": "Local OCR and numbering are heuristic; check equations, options, answer keys and page boundaries.",
        })
    return rows
