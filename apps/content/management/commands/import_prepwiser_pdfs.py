"""Import PrepWiser chapter PDFs into one tenant's private practice bank.

Each PDF supplies its question IDs through links and its answer key on a final
page. The linked public question API is used for math, options and metadata;
answers always come from the PDF key. Worked solutions are not copied.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import pymupdf
import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.audit.models import AuditEvent
from apps.catalog.models import Chapter, Exam, Subject, Topic
from apps.content.figures import clean_image, store
from apps.content.math_text import repair_text
from apps.content.models import Question
from apps.tenancy.models import Tenant


API_ROOT = "https://prepwiser.in/api/v1/questions/"
QUESTION_LINK = re.compile(r"https://prepwiser\.in/questions/([^?#]+)")
KEY_ENTRY = re.compile(r"^\s*(\d+)\s*\.\s*(.*?)\s*$")
QUESTION_TYPES = {
    "MCQ": Question.Type.SINGLE,
    "SINGLE": Question.Type.SINGLE,
    "SINGLE_CORRECT": Question.Type.SINGLE,
    "MSQ": Question.Type.MULTI,
    "MULTI": Question.Type.MULTI,
    "MULTI_CORRECT": Question.Type.MULTI,
    "INTEGER": Question.Type.INTEGER,
    "NUMERICAL": Question.Type.NUMERICAL,
    "ASSERTION_REASON": Question.Type.ASSERTION,
    "MATRIX": Question.Type.MATRIX,
    "MATRIX_MATCH": Question.Type.MATRIX,
    "TRUE_FALSE": Question.Type.SINGLE,
}
SUBJECT_FOLDERS = {
    "mathematics": "Mathematics",
    "maths": "Mathematics",
    "physics": "Physics",
    "chemistry": "Chemistry",
    "biology": "Biology",
}


def _normalized(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


class _QuestionHTML(HTMLParser):
    """Convert API HTML to readable text while retaining math and image refs."""

    BLOCKS = {"p", "div", "li", "tr", "table", "br", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.image_urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.BLOCKS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag == "img":
            url = attrs.get("src", "").strip()
            if url:
                self.image_urls.append(url)
            self.parts.append("\n[See figure]\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCKS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_data(self, data):
        self.parts.append(data)

    def result(self):
        value = "".join(self.parts).replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _html_text(markup):
    parser = _QuestionHTML()
    parser.feed(markup or "")
    return repair_text(parser.result()), parser.image_urls


def _read_pdf(path):
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        raise CommandError(f"Could not open {path}: {exc}") from exc
    question_ids = []
    page_for_id = {}
    key_text = None
    for page_number, page in enumerate(document, start=1):
        text = page.get_text("text")
        if "Answer key:" in text:
            key_text = text
        for link in page.get_links():
            match = QUESTION_LINK.search(link.get("uri", ""))
            if match and match.group(1) not in page_for_id:
                qid = match.group(1)
                question_ids.append(qid)
                page_for_id[qid] = page_number
    document.close()
    if not question_ids:
        raise CommandError(f"No PrepWiser question links found in {path}")
    if not key_text:
        raise CommandError(f"No numbered 'Answer key:' page found in {path}")

    lines = key_text.splitlines()
    answers = {}
    for index, line in enumerate(lines):
        match = KEY_ENTRY.match(line)
        if not match:
            continue
        number, value = int(match.group(1)), match.group(2).strip()
        if not value:
            for later in lines[index + 1 :]:
                value = later.strip()
                if value:
                    break
        if value and not value.startswith("Solve these online"):
            answers[number] = value
    if len(answers) != len(question_ids) or set(answers) != set(range(1, len(question_ids) + 1)):
        raise CommandError(
            f"{path.name}: found {len(question_ids)} question links and {len(answers)} numbered answers"
        )
    return question_ids, page_for_id, answers


def _fetch_question(qid):
    url = API_ROOT + qid
    last_error = None
    for attempt in range(5):
        try:
            response = requests.get(url, timeout=(10, 45))
            if response.status_code == 429 or response.status_code >= 500:
                delay = min(20, int(response.headers.get("Retry-After", 0) or 2**attempt))
                time.sleep(delay)
                continue
            response.raise_for_status()
            payload = response.json().get("data")
            if not isinstance(payload, dict):
                raise ValueError("API response has no question object")
            return qid, payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(min(12, 2**attempt))
    raise CommandError(f"Could not fetch PrepWiser question {qid}: {last_error}")


def _answer_value(raw, question_type):
    """Convert a printed answer key entry to the answer representation used by learning."""
    value = re.sub(r"\s+", "", raw).strip("().")
    if question_type == Question.Type.MULTI:
        parts = [part for part in re.split(r"[,;/]+", value) if part]
        if len(parts) < 2:
            parts = list(value) if len(value) > 1 and value.isalpha() else parts
        return json.dumps([part.casefold() for part in parts])
    return value


def _compare_answers(pdf_answer, api_payload, question_type):
    expected = _answer_value(pdf_answer, question_type)
    correct = api_payload.get("correctOptions")
    if correct is None:
        correct = api_payload.get("integerAnswer")
    if correct is None:
        correct = api_payload.get("trueFalseAnswer")
    if correct is None:
        # The public question endpoint does not expose keys on every question;
        # the PDF key remains the source of truth in that case.
        return
    if isinstance(correct, list):
        api_value = json.dumps([str(item).casefold() for item in correct])
    else:
        api_value = str(correct).strip()
    if question_type == Question.Type.MULTI:
        try:
            same = set(json.loads(expected)) == set(json.loads(api_value))
        except (TypeError, ValueError):
            same = expected.casefold() == api_value.casefold()
    else:
        same = expected.casefold() == api_value.casefold()
    if not same:
        raise CommandError(f"Answer key mismatch for source question {api_payload.get('id')}: PDF={pdf_answer}, API={api_value}")


def _figure_urls(markup, figure_metadata):
    _, inline_urls = _html_text(markup)
    urls = list(inline_urls)
    if isinstance(figure_metadata, dict):
        urls.extend(figure_metadata.keys())
    allowed = []
    for url in dict.fromkeys(urls):
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.hostname in {"jee.prepwiser.in", "prepwiser.in"}:
            allowed.append(url)
    return allowed


class Command(BaseCommand):
    help = "Import all linked PrepWiser JEE Main PYQs and PDF answer keys into a tenant practice bank."

    def add_arguments(self, parser):
        parser.add_argument(
            "--root",
            default=str(Path.home() / "Downloads" / "PrepWiser-JEE-Main-PYQ-PDFs"),
            help="Root folder; PDFs are discovered recursively.",
        )
        parser.add_argument("--tenant", default="demo", help="Target tenant slug (default: demo).")
        parser.add_argument("--workers", type=int, default=16, help="Parallel question fetches (1–32).")
        parser.add_argument(
            "--cache-dir",
            default=str(Path(tempfile.gettempdir()) / "pyq-vault-prepwiser-cache"),
            help="Temporary JSON cache for public API responses, allowing interrupted imports to resume.",
        )
        parser.add_argument(
            "--refresh-figures",
            action="store_true",
            help="Refresh source diagrams for already-imported questions with inline figure markers.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Parse and fetch without database or media writes.")

    def handle(self, *args, **options):
        root = Path(options["root"]).expanduser().resolve()
        workers = options["workers"]
        if not root.is_dir():
            raise CommandError(f"PDF folder does not exist: {root}")
        if not 1 <= workers <= 32:
            raise CommandError("--workers must be between 1 and 32")
        files = sorted(root.rglob("*.pdf"))
        if not files:
            raise CommandError(f"No PDFs found under {root}")
        tenant = Tenant.objects.filter(slug=options["tenant"]).first()
        if tenant is None:
            raise CommandError(f"Unknown tenant slug: {options['tenant']}")

        pdf_rows = []
        by_qid = {}
        for path in files:
            ids, pages, answers = _read_pdf(path)
            subject_name = SUBJECT_FOLDERS.get(path.parent.name.casefold())
            if not subject_name:
                raise CommandError(f"Unknown subject folder for {path}: expected Mathematics, Physics or Chemistry")
            if len(ids) != len(set(ids)):
                raise CommandError(f"{path.name}: duplicate question link IDs in the PDF")
            rows = []
            for number, qid in enumerate(ids, start=1):
                row = {
                    "qid": qid,
                    "answer": answers[number],
                    "number": number,
                    "page": pages[qid],
                    "pdf": path,
                    "subject_name": subject_name,
                }
                rows.append(row)
                prior = by_qid.get(qid)
                if prior and _normalized(prior["answer"]) != _normalized(row["answer"]):
                    raise CommandError(f"Conflicting answer keys for question {qid} in {prior['pdf'].name} and {path.name}")
                by_qid.setdefault(qid, row)
            pdf_rows.append((path, rows))
        self.stdout.write(f"Found {len(files)} PDFs, {sum(len(rows) for _, rows in pdf_rows)} linked questions, {len(by_qid)} unique question IDs.")

        existing = {
            question.source.rsplit("/", 1)[-1]: question
            for question in Question.objects.filter(
                tenant=tenant, source__startswith="https://prepwiser.in/questions/"
            ).only("id", "source", "answer", "question_type", "stem", "figure_paths")
        }
        refresh_qids = {
            qid for qid, question in existing.items()
            if options["refresh_figures"] and "[See figure]" in question.stem
        }
        cache_dir = Path(options["cache_dir"]).expanduser()
        cache_dir.mkdir(parents=True, exist_ok=True)
        payloads = {}
        to_fetch = []
        for qid in by_qid:
            if qid in existing and qid not in refresh_qids:
                continue
            cached = cache_dir / f"{qid}.json"
            if cached.is_file():
                try:
                    payloads[qid] = json.loads(cached.read_text())
                    continue
                except (OSError, ValueError):
                    cached.unlink(missing_ok=True)
            to_fetch.append(qid)
        self.stdout.write(f"{len(existing)} source questions already belong to {tenant.slug}; fetching {len(to_fetch)} new question records.")
        completed = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_question, qid): qid for qid in to_fetch}
            for future in as_completed(futures):
                qid, payload = future.result()
                payloads[qid] = payload
                cached = cache_dir / f"{qid}.json"
                temporary = cached.with_suffix(".tmp")
                temporary.write_text(json.dumps(payload, ensure_ascii=False))
                temporary.replace(cached)
                completed += 1
                if completed % 500 == 0 or completed == len(to_fetch):
                    self.stdout.write(f"Fetched {completed}/{len(to_fetch)} question records.")

        records = []
        taxonomy_pairs = set()
        for qid, row in by_qid.items():
            if qid in existing:
                old = existing[qid]
                if _normalized(old.answer) != _normalized(_answer_value(row["answer"], old.question_type)):
                    raise CommandError(f"Existing answer for {qid} differs from this PDF answer key.")
                if qid not in refresh_qids:
                    continue
            payload = payloads[qid]
            api_subject = (payload.get("subject") or {}).get("name", "")
            if _normalized(api_subject) != _normalized(row["subject_name"]):
                raise CommandError(f"Subject mismatch for {qid}: folder={row['subject_name']}, source={api_subject}")
            api_type = str(payload.get("type", "")).upper()
            question_type = QUESTION_TYPES.get(api_type)
            if question_type is None:
                raise CommandError(f"Unsupported question type {api_type!r} for {qid}")
            chapter_name = (payload.get("chapter") or {}).get("name", "").strip()
            topic_name = (payload.get("topic") or {}).get("name", "").strip()
            if not chapter_name or not topic_name:
                raise CommandError(f"Missing chapter or topic metadata for {qid}")
            _compare_answers(row["answer"], payload, question_type)
            stem, inline_images = _html_text((payload.get("content") or {}).get("data", ""))
            if not stem:
                raise CommandError(f"Empty question stem for {qid}")
            option_rows = []
            for option in payload.get("options") or []:
                option_text, _ = _html_text(option.get("text", ""))
                option_rows.append({"id": str(option.get("id", "")).casefold(), "text": option_text})
            if question_type in (Question.Type.SINGLE, Question.Type.MULTI) and not option_rows:
                raise CommandError(f"Missing answer options for {qid}")
            taxonomy_pairs.add((row["subject_name"], chapter_name, topic_name))
            pyq = payload.get("pyqSource") or {}
            year = str(pyq.get("year") or "")
            date = str(pyq.get("date") or "")
            shift = str(pyq.get("shift") or "")
            source_topic = topic_name
            tags = list(dict.fromkeys(filter(None, [
                "JEE Main", row["subject_name"], chapter_name, topic_name,
                row["pdf"].stem, year, date, f"{shift} shift" if shift else "",
                f"Q{row['number']}",
            ])))
            section = f"JEE Main {year} · {date} · {shift} · {source_topic} · Q{row['number']}"[:80]
            figure_urls = _figure_urls((payload.get("content") or {}).get("data", ""), payload.get("figures"))
            records.append({
                **row, "api": payload, "subject_name": row["subject_name"],
                "chapter_name": chapter_name, "topic_name": topic_name,
                "question_type": question_type, "stem": stem, "options": option_rows,
                "answer_value": _answer_value(row["answer"], question_type),
                "difficulty": {"EASY": 2, "MEDIUM": 3, "HARD": 4}.get(str(payload.get("difficulty", "MEDIUM")).upper(), 3),
                "difficulty_basis": "PrepWiser source difficulty label mapped to the app 1–5 scale." if payload.get("difficulty") else "Moderate default; no source difficulty label was supplied.",
                "taxonomy_basis": "Source subject, chapter and topic from PrepWiser question metadata.",
                "tags": tags, "section": section,
                "figure_urls": figure_urls,
                "existing_question": existing.get(qid),
            })

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS(
                f"Dry run complete: {len(records) - len(refresh_qids)} new questions ready; "
                f"{len(existing) - len(refresh_qids)} already present; {len(refresh_qids)} figures to refresh; "
                f"{len(taxonomy_pairs)} subject/chapter/topic paths; {sum(len(r['figure_urls']) for r in records)} figure references. "
                "No database or media writes made."
            ))
            return

        exam = Exam.objects.filter(slug="jee-main").first()
        if exam is None:
            raise CommandError("The JEE Main exam is missing. Create or seed the exam catalog before importing.")
        subjects = {s.name: s for s in Subject.objects.filter(name__in={r["subject_name"] for r in records})}
        for name in {r["subject_name"] for r in records} - subjects.keys():
            subject, _ = Subject.objects.get_or_create(name=name, defaults={"slug": slugify(name)})
            subjects[name] = subject

        chapter_rows = {(r["subject_name"], r["chapter_name"]) for r in records}
        chapters = {}
        chapters_to_create = []
        for subject_name in {row[0] for row in chapter_rows}:
            subject = subjects[subject_name]
            available = list(Chapter.objects.filter(subject=subject))
            by_name = {_normalized(item.name): item for item in available}
            for _, name in (pair for pair in chapter_rows if pair[0] == subject_name):
                match = by_name.get(_normalized(name))
                if match:
                    chapters[(subject_name, name)] = match
                else:
                    chapter = Chapter(subject=subject, name=name)
                    chapters_to_create.append(chapter)
                    by_name[_normalized(name)] = chapter
                    chapters[(subject_name, name)] = chapter
        if chapters_to_create:
            Chapter.objects.bulk_create(chapters_to_create, batch_size=500)

        topic_rows = {(r["subject_name"], r["chapter_name"], r["topic_name"]) for r in records}
        topics = {}
        topics_to_create = []
        for subject_name, chapter_name in {(a, b) for a, b, _ in topic_rows}:
            chapter = chapters[(subject_name, chapter_name)]
            by_name = {_normalized(item.name): item for item in Topic.objects.filter(chapter=chapter)}
            for _, _, name in (pair for pair in topic_rows if pair[:2] == (subject_name, chapter_name)):
                match = by_name.get(_normalized(name))
                if match:
                    topics[(subject_name, chapter_name, name)] = match
                else:
                    topic = Topic(chapter=chapter, name=name)
                    topics_to_create.append(topic)
                    by_name[_normalized(name)] = topic
                    topics[(subject_name, chapter_name, name)] = topic
        if topics_to_create:
            Topic.objects.bulk_create(topics_to_create, batch_size=500)

        image_cache = {}
        image_failures = 0
        image_urls = list(dict.fromkeys(url for record in records for url in record["figure_urls"][:8]))

        def fetch_figure(url):
            for attempt in range(3):
                try:
                    response = requests.get(url, timeout=(10, 45))
                    response.raise_for_status()
                    raw = response.content
                    if "svg" in response.headers.get("content-type", "").casefold() or url.casefold().endswith(".svg"):
                        svg_document = pymupdf.open(stream=raw, filetype="svg")
                        page = svg_document[0]
                        rect = page.rect
                        scale = min(1.0, 1800 / max(rect.width, rect.height, 1))
                        raw = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
                        svg_document.close()
                    image_bytes, extension = clean_image(raw, "PrepWiser question figure")
                    path = store(image_bytes, extension, f"tenant-{tenant.pk}/prepwiser")
                    return url, path
                except Exception:
                    if attempt < 2:
                        time.sleep(attempt + 1)
            return url, ""

        if image_urls:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(fetch_figure, url) for url in image_urls]
                for index, future in enumerate(as_completed(futures), start=1):
                    url, path = future.result()
                    image_cache[url] = path
                    if not path:
                        image_failures += 1
                    if index % 100 == 0 or index == len(futures):
                        self.stdout.write(f"Fetched {index}/{len(futures)} figure images.")
        questions = []
        refreshed_questions = []
        for record in records:
            figure_paths = []
            for url in record["figure_urls"][:8]:
                if image_cache[url]:
                    figure_paths.append(image_cache[url])
            stem = record["stem"]
            if record["figure_urls"] and len(figure_paths) < len(record["figure_urls"]):
                stem += "\n[Some source figures could not be copied.]"
            if record["existing_question"]:
                existing_question = record["existing_question"]
                existing_question.stem = stem
                existing_question.figure_paths = figure_paths
                refreshed_questions.append(existing_question)
                continue
            pyq = record["api"].get("pyqSource") or {}
            year = str(pyq.get("year") or "")
            date = str(pyq.get("date") or "")
            questions.append(Question(
                tenant=tenant,
                kind=Question.Kind.PRACTICE,
                exam=exam,
                source_year=int(year) if year.isascii() and year.isdecimal() and len(year) == 4 else None,
                section=record["section"],
                question_type=record["question_type"],
                stem=stem,
                options=record["options"],
                answer=record["answer_value"],
                solution="",
                topic=topics[(record["subject_name"], record["chapter_name"], record["topic_name"])],
                source=f"https://prepwiser.in/questions/{record['qid']}",
                license="PrepWiser PDF source; original question copyright remains with its rights holder.",
                status=Question.Status.PUBLISHED,
                difficulty=record["difficulty"],
                difficulty_basis=record["difficulty_basis"],
                taxonomy_basis=record["taxonomy_basis"],
                tags=record["tags"],
                source_page=record["page"],
                figure_paths=figure_paths,
            ))

        with transaction.atomic():
            if questions:
                from apps.content.models import without_duplicates
                Question.objects.bulk_create(without_duplicates(questions), batch_size=500)
            if refreshed_questions:
                for question in refreshed_questions:
                    question.refresh_fingerprint()
                Question.objects.bulk_update(refreshed_questions, ["stem", "figure_paths", "fingerprint"], batch_size=500)
            AuditEvent.objects.create(
                tenant=tenant,
                action="prep_wiser_pdf_folder_imported",
                object_type="Question",
                object_id="prep-wiser-jee-main-pdf-folder",
                after={
                    "root": str(root), "pdf_count": len(files),
                    "linked_question_count": sum(len(rows) for _, rows in pdf_rows),
                    "unique_question_ids": len(by_qid), "created": len(questions),
                    "already_present": len(existing) - len(refresh_qids),
                    "figures_refreshed": len(refreshed_questions),
                    "figure_fetch_failures": image_failures,
                },
            )
        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(questions)} questions from {len(files)} PDFs into tenant '{tenant.slug}'. "
            f"Skipped {len(existing) - len(refresh_qids)} already present; refreshed {len(refreshed_questions)} figure records; "
            f"figure fetch failures: {image_failures}."
        ))
