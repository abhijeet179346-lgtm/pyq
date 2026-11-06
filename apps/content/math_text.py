"""Conservative repair of imported question text for the existing MathJax renderer."""
import re


_BROKEN = re.compile(r"(?:Ã.|Â.|Î.|Ï.|â[\x80-\xff])")
_ENV_TOKEN = re.compile(r"\\(begin|end)\{(array|matrix|pmatrix|bmatrix|cases|aligned|align)\}")
_MATH = re.compile(r"(\$\$.*?\$\$|(?<!\\)\$(?:\\.|[^$])*?\$|\\\[.*?\\\]|\\\(.*?\\\))", re.S)


def repair_text(value):
    if not isinstance(value, str) or not value:
        return value
    if _BROKEN.search(value):
        for encoding in ('latin-1', 'cp1252'):
            try:
                fixed = value.encode(encoding).decode('utf-8')
            except (UnicodeError, ValueError):
                continue
            if len(_BROKEN.findall(fixed)) < len(_BROKEN.findall(value)):
                value = fixed
                break
    value = value.replace('Â\xa0', '\xa0')
    value = re.sub(r'Â(?=\s|$)', '', value)
    value = value.replace('â\x80\x93', '–').replace('â\x80\x8b', '')
    # MathJax only processes environments inside math delimiters. Avoid touching
    # segments already delimited; prose around a table remains ordinary text.
    pieces = _MATH.split(value)
    for index in range(0, len(pieces), 2):
        pieces[index] = _wrap_environments(pieces[index])
    return ''.join(pieces)


def _wrap_environments(value):
    result = []
    stack = []
    start = cursor = 0
    for match in _ENV_TOKEN.finditer(value):
        kind, name = match.groups()
        if kind == 'begin':
            if not stack:
                start = match.start()
            stack.append(name)
        elif stack and stack[-1] == name:
            stack.pop()
            if not stack:
                result.append(value[cursor:start])
                result.append('\\[' + value[start:match.end()] + '\\]')
                cursor = match.end()
        else:
            # Keep malformed LaTeX for editorial review.
            return value
    return ''.join(result) + value[cursor:] if not stack else value


def repair_question_fields(question):
    changes = {}
    for field in ('stem', 'solution'):
        value = getattr(question, field)
        fixed = repair_text(value)
        if fixed != value:
            changes[field] = fixed
    options = question.options
    if isinstance(options, list):
        fixed = [{**item, 'text': repair_text(item.get('text', ''))} if isinstance(item, dict) else repair_text(item)
                 for item in options]
        if fixed != options:
            changes['options'] = fixed
    return changes
