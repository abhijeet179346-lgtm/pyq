"""PDF -> review workbook extraction for admin content jobs.

Extraction is a review aid. Rows are never published directly: the admin
downloads the workbook, fills exam/subject/taxonomy, checks every row and
re-uploads it as an Excel questions job.
"""
import os
import re

import pymupdf as fitz
from django.utils import timezone

from .models import ContentJob

ROW_KEYS = ['option_a', 'option_b', 'option_c', 'option_d', 'answer', 'solution', 'subject', 'chapter',
            'topic', 'subtopic', 'difficulty', 'exam', 'year']
TEXT_WARNING = 'Check numbering, equations, continuation and answer key. No answer inferred.'
LOCAL_OCR_WARNING = ('Local OCR output (no formula model). Verify equations, figures, options and answers '
                     'against the source page.')
VISION_WARNING = 'OCR output. Verify all equations, figures and answers against the source page.'

# "1." / "12:" / "Q1." / "Q.1)" / "Question 3:" / "Q No. 4".
QUESTION_START = re.compile(
    r'(?:^|(?<=\s))(?:Q(?:uestion)?\s*\.?\s*(?:No\.?\s*)?(\d{1,4})\s*[.):：]?|(\d{1,4})\s*[.:：])\s+(?=\S)',
    re.IGNORECASE | re.MULTILINE)
# "(A)" / "(a)" / "(1)" anywhere after whitespace; bare "A." / "a)" likewise.
OPTION_MARK = re.compile(r'(?:^|(?<=\s))(?:\(([A-Da-d1-4])\)|([A-Da-d])[.)])\s*(?=\S)', re.MULTILINE)
ANSWER = re.compile(
    r'(?:^|(?<=\s))(?:correct\s+)?ans(?:wer)?\s*(?:is\b|[:：.\-–])\s*[:：.\-–]?\s*\(?'
    r'((?:[A-Da-d1-4]\s*,\s*)*[A-Da-d1-4](?![\w.])|[-+]?\d+(?:\.\d+)?)\)?',
    re.IGNORECASE)
SOLUTION = re.compile(r'(?:^|(?<=\s))(?:solution|explanation|sol|exp)\s*[.:：\-–]\s*', re.IGNORECASE)
MARKDOWN_NOISE = re.compile(r'(?m)^\s*(?:[-*•]|#{1,6})\s+|\*\*|__')
LETTERS = 'ABCD'


def page_text(page):
    """Page text in reading order; two-column pages are read left column first."""
    blocks = [b for b in page.get_text('blocks', sort=True) if b[6] == 0 and b[4].strip()]
    if not blocks:
        return ''
    width, height = page.rect.width, page.rect.height
    mid, tol = width / 2, width * 0.03

    def is_left(b): return b[2] <= mid + tol
    def is_right(b): return b[0] >= mid - tol and b[2] > mid + tol
    def covered(bs): return sum(b[3] - b[1] for b in bs)

    left, right = [b for b in blocks if is_left(b)], [b for b in blocks if is_right(b)]
    if not (covered(left) > 0.3 * height and covered(right) > 0.3 * height):
        return page.get_text('text', sort=True).strip()
    out, segment = [], []

    def flush():
        for column in (is_left, is_right):
            out.extend(b[4] for b in sorted((b for b in segment if column(b)), key=lambda b: (b[1], b[0])))
        segment.clear()

    for block in sorted(blocks, key=lambda b: (b[1], b[0])):
        if is_left(block) or is_right(block):
            segment.append(block)
        else:  # full-width header/instructions separate column runs
            flush()
            out.append(block[4])
    flush()
    return '\n'.join(text.strip() for text in out).strip()


def _question_starts(text):
    starts, last = [], None
    for match in QUESTION_START.finditer(text):
        number = int(match[1] or match[2])
        at_line_start = match.start() == 0 or text[match.start() - 1] == '\n'
        if last is None:
            ok = at_line_start
        elif at_line_start:
            ok = last < number <= last + 5
        else:
            # Mid-line starts (OCR joins lines) only once the previous question looks complete.
            previous = text[starts[-1][0].end():match.start()]
            ok = number == last + 1 and (ANSWER.search(previous) or _options(previous)[1])
        if ok:
            starts.append((match, number))
            last = number
    return starts


def _options(block):
    """Return (stem_end, [(letter, start, end)]) for the best A-D or 1-4 option sequence."""
    marks = [(m[1] or m[2], m) for m in OPTION_MARK.finditer(block)]
    for labels in (LETTERS, '1234'):
        chosen = []
        for label, match in marks:
            label = label.upper()
            if len(chosen) < 4 and label == labels[len(chosen)]:
                chosen.append(match)
        if len(chosen) >= 2:
            spans = []
            for pos, match in enumerate(chosen):
                end = chosen[pos + 1].start() if pos + 1 < len(chosen) else len(block)
                spans.append((LETTERS[pos], match.end(), end))
            return chosen[0].start(), spans, labels
    return None, [], LETTERS


def _normalise_answer(raw, labels, has_options):
    parts = [part.strip().upper() for part in raw.split(',')]
    if has_options and labels == '1234' and all(part in '1234' for part in parts):
        parts = [LETTERS['1234'.index(part)] for part in parts]
    if has_options and not all(part in LETTERS for part in parts):
        return raw, 'Printed answer does not match option labels.'
    return ','.join(parts), ''


def split_questions(text, page, job, warning=TEXT_WARNING):
    text = MARKDOWN_NOISE.sub('', text.replace('\r', ''))
    starts = _question_starts(text)
    rows = []
    for index, (match, number) in enumerate(starts):
        block = text[match.end():starts[index + 1][0].start() if index + 1 < len(starts) else len(text)].strip()
        if len(block) < 12:
            continue
        original = block
        solution = ''
        solution_match = SOLUTION.search(block)
        if solution_match:
            solution, block = block[solution_match.end():].strip(), block[:solution_match.start()].strip()
        printed = ANSWER.search(block)
        raw_answer = printed[1] if printed else ''
        if printed:
            block = (block[:printed.start()] + ' ' + block[printed.end():]).strip()
        stem_end, spans, labels = _options(block)
        stem = (block[:stem_end] if spans else block).strip()
        row = {key: '' for key in ROW_KEYS}
        notes = [warning]
        answer, note = _normalise_answer(raw_answer, labels, bool(spans)) if raw_answer else ('', '')
        if note:
            notes.append(note)
        row.update(source_id=f'pdf-{job.pk}-p{page}-q{number}', kind='PRACTICE',
                   question_type=('multi_correct' if ',' in answer else 'single_correct') if spans else 'numerical',
                   stem=stem, answer=answer, solution=solution, source=job.source, license=job.license,
                   source_page=page, review_status='needs_review', warnings=' '.join(notes), original_text=original)
        for letter, start, end in spans:
            row['option_' + letter.lower()] = block[start:end].strip()
        rows.append(row)
    return rows


def _vision_rows(job, image, page_number, text):
    from apps.scans.services import _request_page
    result = _request_page(image, [], tenant=job.tenant, user=job.initiated_by)
    rows = []
    for number, item in enumerate(result.get('questions', []), 1):
        values = {key: '' for key in ['subject', 'chapter', 'topic', 'subtopic', 'exam', 'year']}
        values.update(source_id=f'ocr-{job.pk}-p{page_number}-{number}', kind='PRACTICE', stem=item.get('stem', ''),
                      answer=item.get('answer', ''), solution=item.get('solution', ''),
                      question_type=item.get('question_type', 'single_correct'), difficulty=item.get('difficulty', ''),
                      source=job.source, license=job.license, source_page=page_number, review_status='needs_review',
                      warnings=VISION_WARNING, original_text=text)
        for pos, option in enumerate(item.get('options', [])[:4]):
            values['option_' + chr(97 + pos)] = str(option.get('text', '')) if isinstance(option, dict) else str(option)
        rows.append(values)
    return rows


def _ocr_pages(job, pdf, indexes):
    """OCR image-only pages. Returns {index: (rows, page_text, page_warning)}."""
    results, local = {}, []
    use_vision = job.use_ocr and os.environ.get('OPENAI_API_KEY')
    for index in indexes:
        if not use_vision:
            local.append(index)
            continue
        image = pdf[index].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes('png')
        try:
            results[index] = (_vision_rows(job, image, index + 1, ''), '', 'Vision OCR; human review required.')
        except Exception as exc:  # budget/provider errors fall back to local OCR
            local.append(index)
            results[index] = ([], '', f'Vision OCR failed ({str(exc)[:200]}); used local OCR.')
    if not local:
        return results
    try:
        from apps.scans.docling_adapter import extract_page_markdown
        chunk = fitz.open()
        for index in local:
            chunk.insert_pdf(pdf, from_page=index, to_page=index)
        pages = extract_page_markdown(chunk.tobytes(), formulas=False)
        chunk.close()
        if len(pages) != len(local):
            raise RuntimeError('Local OCR returned an unexpected number of pages.')
    except Exception as exc:
        reason = ('Local OCR is not installed; run pip install -r requirements-ocr.txt on the worker, '
                  'or tick "Use OCR" with an OpenAI key and AI budget.') if isinstance(exc, (ImportError, RuntimeError)) \
            and 'not installed' in str(exc) else f'Local OCR failed: {str(exc)[:300]}'
        for index in local:
            results[index] = ([], '', reason)
        return results
    for index, markdown in zip(local, pages):
        previous = results.get(index, ([], '', ''))[2]
        warning = (previous + ' ' if previous else '') + 'Local OCR; human review required.'
        results[index] = (split_questions(markdown, index + 1, job, LOCAL_OCR_WARNING), markdown, warning)
    return results


def process_pdf_chunk(job, page_batch=15, ocr_page_limit=3):
    """Process the next batch of pages. Scanned pages are OCR'd, at most ``ocr_page_limit`` per call."""
    if job.kind != 'pdf':
        return
    claimed = ContentJob.objects.filter(pk=job.pk, status__in=['queued', 'failed']).update(
        status='processing', updated_at=timezone.now())
    if not claimed:
        job.refresh_from_db()
        return
    job.refresh_from_db()
    job.status = 'processing'
    try:
        job.file.open('rb')
        pdf = fitz.open(stream=job.file.read(), filetype='pdf')
        if pdf.is_encrypted:
            raise ValueError('Password-protected PDFs must be unlocked before upload.')
        if len(pdf) > 1500:
            raise ValueError('Maximum 1500 pages per job; split larger PDFs.')
        job.page_count = len(pdf)
        stop = min(job.next_page + page_batch, len(pdf))
        texts, scanned = {}, []
        for index in range(job.next_page, stop):
            text = page_text(pdf[index])
            if len(text) < 30:
                if len(scanned) >= ocr_page_limit:
                    stop = index
                    break
                scanned.append(index)
            texts[index] = text
        ocr = _ocr_pages(job, pdf, scanned) if scanned else {}
        for index in range(job.next_page, stop):
            if index in ocr:
                rows, text, warning = ocr[index]
                text = text or texts[index]
            else:
                text = texts[index]
                rows = split_questions(text, index + 1, job)
                warning = 'Question segmentation is heuristic; check source page.'
            job.rows.extend(rows)
            job.pages.append({'page': index + 1, 'text': text, 'warnings': warning})
            job.next_page = index + 1
        pdf.close()
        job.status = 'review' if job.next_page >= job.page_count else 'queued'
        job.errors = []
        job.save(update_fields=['page_count', 'next_page', 'rows', 'pages', 'status', 'errors', 'updated_at'])
    except Exception as exc:
        job.status = 'failed'
        job.errors = [str(exc)[:1000]]
        job.save(update_fields=['status', 'errors', 'updated_at'])
    finally:
        job.file.close()
