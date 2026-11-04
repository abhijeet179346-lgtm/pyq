import json
import re


def normalize_answer(value):
    return re.sub(r"\s+", "", str(value or "").replace("$", "")).casefold()


def option_pairs(question):
    result = []
    for index, option in enumerate(question.options or []):
        default_key = chr(ord("A") + index)
        if isinstance(option, dict):
            key = str(option.get("id") or option.get("key") or option.get("label") or default_key)
            text = str(option.get("text") or option.get("value") or option.get("label") or option)
        else:
            key, text = default_key, str(option)
        result.append((key, text))
    return result


def is_correct(question, selected_values):
    selected = {normalize_answer(value) for value in selected_values if value not in (None, "")}
    try:
        decoded = json.loads(question.answer)
        expected_values = decoded if isinstance(decoded, list) else [decoded]
    except (ValueError, TypeError):
        expected_values = [question.answer]
    expected = {normalize_answer(value) for value in expected_values}
    if not selected:
        return False
    option_lookup = {normalize_answer(key): normalize_answer(text) for key, text in option_pairs(question)}
    expanded = {option_lookup.get(value, value) for value in selected}
    # Accept either stored option IDs (A/B/...) or the literal option value.
    return selected == expected or expanded == expected
