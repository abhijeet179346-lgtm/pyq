import base64
import json
import os
import urllib.request
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Topic
from apps.content.models import Question
from apps.audit.models import AuditEvent
from .models import ScanImport, TenantOCRSettings


def _save_figure_crops(scan, page_number, image_bytes, boxes):
    from PIL import Image
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    paths = []
    for index, box in enumerate(boxes[:4]):
        if not isinstance(box, list) or len(box) != 4:
            continue
        try: x0, y0, x1, y1 = [float(value) for value in box]
        except (ValueError, TypeError): continue
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            continue
        crop = image.crop((int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height)))
        stream = BytesIO()
        crop.save(stream, format="PNG")
        path = f"tenant-{scan.tenant_id}/scan-pages/{scan.pk}/page-{page_number}-figure-{index + 1}.png"
        if not default_storage.exists(path):
            path = default_storage.save(path, ContentFile(stream.getvalue()))
        paths.append(path)
    return paths


def _request_page(image_bytes, topics, tenant=None, user=None, operation="scan_ocr", layout_text=""):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured for scan extraction.")
    ai_settings = None
    if tenant:
        from django.db.models import Sum
        from django.utils import timezone
        from apps.tutoring.models import AIUsage, TenantAISettings
        ai_settings = TenantAISettings.objects.filter(tenant=tenant).first()
        if ai_settings is None or ai_settings.monthly_budget_usd <= 0:
            raise RuntimeError("Set a non-zero monthly AI budget for this institute before using scan extraction.")
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = AIUsage.objects.filter(tenant=tenant, created_at__gte=month_start).aggregate(value=Sum("cost_usd"))["value"] or 0
        if spent >= ai_settings.monthly_budget_usd:
            raise RuntimeError("This institute has reached its monthly AI budget.")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = (ai_settings.vision_model if ai_settings else "") or os.environ.get("PYQ_VISION_MODEL", "gpt-4o-mini")
    data_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    prompt = (
        "Extract every educational multiple-choice or numeric question visible on this page. "
        "Read columns in order. Keep each printed answer and explanation with its question. "
        "Preserve equations in LaTeX. Treat printed exam/year/shift labels as metadata, not as part of the stem. "
        "Return JSON only as {\"questions\":[{\"question_type\":\"single_correct|multi_correct|integer|numerical|matrix_match|assertion_reason\","
        "\"stem\":\"Markdown and LaTeX\",\"options\":[{\"id\":\"printed option label\",\"text\":\"...\"}],"
        "\"answer\":\"...\",\"solution\":\"...\",\"difficulty\":1,\"topic_id\":123,"
        "\"exam\":\"...\",\"year\":\"...\",\"sitting\":\"...\","
        "\"figure_bboxes\":[[0.1,0.2,0.8,0.7]]}]} . "
        "Never invent an answer or solution: leave it empty if not printed or confidently readable. "
        "Choose topic_id only from this allowed list; use null if uncertain. Include figure_bboxes as normalized [x0,y0,x1,y1] arrays for diagrams/tables needed to understand the question; use [] when none.\n"
        + json.dumps(topics, ensure_ascii=False)
        + ("\nLayout/OCR transcription for reference; verify against the image:\n" + layout_text[:18000] if layout_text else "")
    )
    payload = {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
        ]}]}
    request = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read())
    if tenant:
        from apps.tutoring.services import record_ai_usage
        usage = result.get("usage", {})
        record_ai_usage(tenant=tenant, user=user, operation=operation, model=model,
            input_tokens=int(usage.get("prompt_tokens", 0)), output_tokens=int(usage.get("completion_tokens", 0)))
    return json.loads(result["choices"][0]["message"]["content"])


def process_scan(scan, page_batch=None):
    """Process a bounded page batch; save progress and tenant-scoped review rows."""
    from django.db import transaction
    import fitz

    claimed = ScanImport.objects.filter(
        pk=scan.pk, status__in=[ScanImport.Status.QUEUED, ScanImport.Status.FAILED]
    ).update(status=ScanImport.Status.PROCESSING, errors=[])
    if not claimed:
        scan.refresh_from_db()
        return scan
    scan.refresh_from_db()
    settings = TenantOCRSettings.objects.filter(tenant=scan.tenant).first()
    backend = settings.backend if settings else TenantOCRSettings.Backend.VISION
    batch_size = page_batch or (settings.page_batch_size if settings else 10)
    batch_size = max(1, min(int(batch_size), 25))
    try:
        scan.file.open("rb")
        raw = scan.file.read()
        filename = scan.file_name.lower()
        if filename.endswith(".pdf"):
            doc = fitz.open(stream=raw, filetype="pdf")
            if doc.is_encrypted:
                raise ValueError("Password-protected PDFs must be unlocked before upload.")
            if not 0 < len(doc) <= 1500:
                raise ValueError("PDFs must have 1 to 1500 pages; split larger PDFs.")
            total_pages = len(doc)
        else:
            doc = None
            total_pages = 1
        if scan.page_count and scan.page_count != total_pages:
            raise ValueError("The uploaded file changed after processing started.")
        scan.page_count = total_pages
        start_page = scan.next_page
        stop_page = min(start_page + batch_size, total_pages)
        layout_pages = [""] * (stop_page - start_page)
        if backend in (TenantOCRSettings.Backend.DOCLING_LOCAL,
                       TenantOCRSettings.Backend.DOCLING_VISION):
            from .docling_adapter import extract_page_markdown
            chunk = fitz.open()
            if doc is not None:
                chunk.insert_pdf(doc, from_page=start_page, to_page=stop_page - 1)
            else:
                image_doc = fitz.open(stream=raw,
                    filetype="jpeg" if filename.endswith((".jpg", ".jpeg")) else "png")
                pdf_image = fitz.open("pdf", image_doc.convert_to_pdf())
                chunk.insert_pdf(pdf_image)
                pdf_image.close()
                image_doc.close()
            layout_pages = extract_page_markdown(chunk.tobytes())
            chunk.close()
            if len(layout_pages) != stop_page - start_page:
                raise RuntimeError("Docling returned an unexpected number of pages.")
        allowed_queryset = Topic.objects.select_related("chapter__subject")
        if scan.initiated_by and scan.initiated_by.role == "teacher":
            from apps.learning.models import TeacherSubjectAssignment
            subject_ids = TeacherSubjectAssignment.objects.filter(
                tenant=scan.tenant, teacher=scan.initiated_by
            ).values_list("subject_id", flat=True)
            allowed_queryset = allowed_queryset.filter(chapter__subject_id__in=subject_ids)
        topic_rows = list(allowed_queryset.values(
            "id", "name", "chapter__name", "chapter__subject__name"
        ))
        topics = [{"id": row["id"], "name": row["name"],
                   "chapter": row["chapter__name"], "subject": row["chapter__subject__name"]}
                  for row in topic_rows]
        topic_map = {str(row["id"]): row for row in topics}
        for index in range(start_page, stop_page):
            page_number = index + 1
            if doc is None:
                if filename.endswith((".jpg", ".jpeg")):
                    from PIL import Image
                    converted = BytesIO()
                    Image.open(BytesIO(raw)).convert("RGB").save(converted, format="PNG")
                    image = converted.getvalue()
                else:
                    image = raw
                extension = "png"
            else:
                image = doc[index].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
                extension = "png"
            page_path = f"tenant-{scan.tenant_id}/scan-pages/{scan.pk}/page-{page_number}.{extension}"
            if not default_storage.exists(page_path):
                page_path = default_storage.save(page_path, ContentFile(image))
            if backend == TenantOCRSettings.Backend.DOCLING_LOCAL:
                from .local_questions import extract_local_questions
                questions = extract_local_questions(layout_pages[index - start_page], topics, scan.file_name)
            else:
                result = _request_page(image, topics, tenant=scan.tenant, user=scan.initiated_by,
                                       layout_text=layout_pages[index - start_page])
                questions = result.get("questions")
            if not isinstance(questions, list):
                raise ValueError(f"Page {page_number}: OCR response has no question list.")
            page_rows = []
            for question in questions:
                if not isinstance(question, dict):
                    continue
                question["source_page"] = page_number
                question["figure_paths"] = _save_figure_crops(
                    scan, page_number, image, question.get("figure_bboxes", []))
                topic = topic_map.get(str(question.get("topic_id")))
                question["topic_id"] = topic["id"] if topic else ""
                question["needs_topic_review"] = topic is None
                tags = []
                if topic:
                    tags = [topic["subject"], topic["chapter"], topic["name"]]
                for key in ("exam", "year", "sitting"):
                    value = str(question.get(key) or "").strip()[:120]
                    if value:
                        tags.append(value)
                suggested = question.get("tags")
                if not isinstance(suggested, list):
                    suggested = []
                question["tags"] = list(dict.fromkeys(
                    tag for tag in tags + suggested if isinstance(tag, str) and tag.strip()))
                page_rows.append(question)
            with transaction.atomic():
                locked = ScanImport.objects.select_for_update().get(pk=scan.pk)
                locked.extraction_payload = [*locked.extraction_payload, *page_rows]
                locked.extracted_count = len(locked.extraction_payload)
                while len(locked.page_image_paths) < page_number:
                    locked.page_image_paths.append("")
                locked.page_image_paths[page_number - 1] = page_path
                locked.next_page = page_number
                locked.page_count = total_pages
                locked.save(update_fields=["extraction_payload", "extracted_count", "page_image_paths",
                                           "next_page", "page_count"])
        if doc is not None:
            doc.close()
        scan.refresh_from_db()
        scan.status = (ScanImport.Status.QUEUED if scan.next_page < total_pages else
                       ScanImport.Status.REVIEW if scan.extracted_count else ScanImport.Status.FAILED)
        scan.errors = [] if scan.extracted_count or scan.next_page < total_pages else ["No questions were extracted."]
        if scan.next_page == total_pages:
            scan.completed_at = timezone.now()
    except Exception as exc:
        scan.refresh_from_db()
        scan.status = ScanImport.Status.FAILED
        scan.errors = [str(exc)[:500]]
    finally:
        scan.file.close()
        scan.save(update_fields=["status", "errors", "completed_at"])
        AuditEvent.objects.create(tenant=scan.tenant, actor=scan.initiated_by,
            action="scan_import_processed", object_type="ScanImport", object_id=str(scan.pk),
            after={"status": scan.status, "pages_processed": scan.next_page,
                   "pages": scan.page_count, "questions": scan.extracted_count, "errors": scan.errors})
    return scan


@transaction.atomic
def approve_extracted(scan, selected_rows, edits, user, allowed_topic_ids=None):
    scan = ScanImport.objects.select_for_update().get(pk=scan.pk)
    if scan.status not in (ScanImport.Status.REVIEW, ScanImport.Status.COMPLETED):
        raise ValueError("Finish extraction before creating draft questions.")
    existing_topic_ids = set(Topic.objects.values_list("id", flat=True))
    questions = []
    accepted_indices = []
    already_accepted = {str(value) for value in scan.accepted_rows}
    for index in selected_rows:
        if str(index) in already_accepted:
            continue
        try:
            item = dict(scan.extraction_payload[int(index)])
        except (ValueError, IndexError, TypeError):
            continue
        original_topic_id = int(item.get("topic_id") or 0)
        item.update(edits.get(str(index), {}))
        topic_id = int(item.get("topic_id") or 0)
        if topic_id not in existing_topic_ids:
            continue
        if allowed_topic_ids is not None and topic_id not in allowed_topic_ids:
            continue
        kind = item.get("question_type")
        if kind not in {choice for choice, _ in Question.Type.choices}:
            continue
        selected_topic = Topic.objects.select_related("chapter__subject").get(pk=topic_id)
        taxonomy = [selected_topic.chapter.subject.name, selected_topic.chapter.name, selected_topic.name]
        previous = Topic.objects.select_related("chapter__subject").filter(pk=original_topic_id).first()
        previous_taxonomy = ([previous.chapter.subject.name, previous.chapter.name, previous.name]
                             if previous else [])
        suggested_tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        reviewed_tags = list(dict.fromkeys(taxonomy + [
            str(tag).strip()[:120] for tag in suggested_tags
            if isinstance(tag, str) and tag.strip() and tag not in previous_taxonomy
        ]))[:20]
        question = Question(tenant=scan.tenant, kind=Question.Kind.PRACTICE, topic_id=topic_id,
            question_type=kind, stem=str(item.get("stem", "")).strip(), options=item.get("options") or [],
            answer=str(item.get("answer", "")).strip(), solution=str(item.get("solution", "")).strip(),
            difficulty=max(1, min(int(item.get("difficulty", 3)), 5)), source=scan.source,
            license=scan.license, status=Question.Status.DRAFT, created_by=user,
            source_scan_id=scan.pk, source_page=item.get("source_page"), figure_paths=item.get("figure_paths", []),
            tags=reviewed_tags,
            taxonomy_basis="OCR suggested tags; human reviewed")
        question.full_clean()
        questions.append(question)
        accepted_indices.append(str(index))
    if not questions:
        raise ValueError("Select at least one valid question and assign each a topic.")
    Question.objects.bulk_create(questions)
    scan.accepted_rows = sorted(already_accepted | set(accepted_indices), key=int)
    scan.accepted_count = len(scan.accepted_rows)
    if scan.accepted_count >= scan.extracted_count:
        scan.status = ScanImport.Status.COMPLETED
    scan.save(update_fields=["accepted_count", "accepted_rows", "status"])
    AuditEvent.objects.create(tenant=scan.tenant, actor=user, action="scan_import_questions_accepted",
        object_type="ScanImport", object_id=str(scan.pk), after={"count": len(questions), "status": scan.status})
    return questions
