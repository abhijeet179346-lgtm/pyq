"""Answer keys for PDF uploads: printed at the end of the paper or supplied as a separate PDF.

Answers are matched to extracted questions by question number. Nothing is guessed: rows whose
number is missing from the key, or whose printed answer disagrees with it, are left for review.
"""
import re

HEADING = re.compile(r'\b(?:answer\s*keys?|answers?\s*(?:sheet|table)|key\s*answers?)\b|^\s*answers?\s*:?\s*$',
                     re.IGNORECASE | re.MULTILINE)
# "1. A" "1) (b)" "Q1 - C" "12: (3)" "5 – 2.5" "7. A,C"  (number, separator, answer).
PAIR = re.compile(
    r'(?<![\w.])(?:Q(?:\.|uestion)?\s*(?:No\.?\s*)?)?(\d{1,3})\s*(?:[.):\-–—=]\s*|\s+)'
    r'(\(\s*[A-Da-d1-4]\s*\)(?:\s*,\s*\(\s*[A-Da-d1-4]\s*\))*'
    r'|[A-Da-d](?:\s*,\s*[A-Da-d])*(?![A-Za-z])'
    r'|[-+]?\d+(?:\.\d+)?(?![\d.]))',
    re.IGNORECASE)
OPTION_LINE = re.compile(r'(?:^|\s)\(?[A-Da-d]\)\s+\w{3,}')
LETTERS = 'ABCD'


def parse_answer_key(text):
    """Return {question number: raw answer} for every number-answer pair in ``text``."""
    key = {}
    for number, answer in PAIR.findall(text or ''):
        answer = re.sub(r'[()\s]', '', answer).upper()
        key.setdefault(int(number), answer)
    return key


def is_answer_key_page(text, previous_was_key=False):
    """A page of mostly number→answer pairs, under an answer-key heading (or continuing one)."""
    pairs = len(parse_answer_key(text))
    if pairs < 5:
        return False
    looks_like_questions = len(OPTION_LINE.findall(text)) >= 4
    return (bool(HEADING.search(text)) or previous_was_key) and not looks_like_questions


def question_number(row):
    match = re.search(r'-q(\d+)$', str(row.get('source_id', '')))
    return int(match[1]) if match else None


def _normalise(raw, has_options):
    parts = raw.split(',')
    if has_options:
        letters = [LETTERS['1234'.index(p)] if p in '1234' else p for p in parts]
        return ','.join(letters) if all(p in LETTERS for p in letters) else None
    try:
        float(raw)
    except ValueError:
        return None
    return raw


def apply_answer_key(rows, key):
    """Fill missing answers from ``key``. Returns a one-line summary for the admin."""
    if not key:
        return 'No answer key found.' if rows else ''
    numbers = [question_number(row) for row in rows]
    duplicates = sorted({n for n in numbers if n is not None and numbers.count(n) > 1})
    if duplicates:
        return (f'Answer key found ({len(key)} answers) but not applied: question numbers repeat in this PDF '
                f'(e.g. {", ".join(map(str, duplicates[:5]))}), so answers cannot be matched safely. '
                'Fill answers in the review workbook.')
    filled = conflicts = unmatched = 0
    for row, number in zip(rows, numbers):
        raw = key.get(number) if number is not None else None
        if raw is None:
            unmatched += not row.get('answer')
            continue
        has_options = bool(row.get('option_a') or row.get('option_b'))
        answer = _normalise(raw, has_options)
        if answer is None:
            row['warnings'] = (row.get('warnings', '') + f' Answer key gives "{raw}", which does not fit this question.').strip()
            continue
        if row.get('answer'):
            if row['answer'].replace(' ', '').upper() != answer:
                conflicts += 1
                row['warnings'] = (row.get('warnings', '') + f' Answer key says {answer}; paper printed {row["answer"]}.').strip()
            continue
        row['answer'] = answer
        if has_options:
            row['question_type'] = 'multi_correct' if ',' in answer else 'single_correct'
        else:
            row['question_type'] = 'numerical'
        row['warnings'] = (row.get('warnings', '') + ' Answer taken from the answer key; verify.').strip()
        filled += 1
    parts = [f'Answer key: {len(key)} answers found, {filled} filled']
    if conflicts:
        parts.append(f'{conflicts} disagree with the printed answer (kept the printed one, see warnings)')
    if unmatched:
        parts.append(f'{unmatched} questions still have no answer')
    return '; '.join(parts) + '.'
