"""Content fingerprints for spotting duplicate questions.

Two questions share a fingerprint when their stem and options read the same after ignoring case,
spacing, punctuation and LaTeX formatting noise, so re-uploads with cosmetic differences collide.
"""
import hashlib
import re
import unicodedata

LATEX_NOISE = re.compile(r'\\(?:left|right|displaystyle|textstyle|mathrm|text|mathbf|rm|,|;|!|quad|qquad)\b|\$')
NON_WORD = re.compile(r'[^0-9a-z]+')


def normalise(text):
    text = unicodedata.normalize('NFKC', str(text or '')).casefold()
    text = LATEX_NOISE.sub(' ', text)
    return NON_WORD.sub(' ', text).strip()


def option_texts(options):
    texts = []
    for option in options or []:
        texts.append(option.get('text', '') if isinstance(option, dict) else option)
    return sorted(normalise(text) for text in texts if normalise(text))


def question_fingerprint(stem, options=None):
    stem = normalise(stem)
    if len(stem) < 12:
        return ''  # too short to compare reliably
    payload = stem + '\x1f' + '\x1e'.join(option_texts(options))
    return hashlib.sha256(payload.encode()).hexdigest()
