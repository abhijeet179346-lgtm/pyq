"""Publish the conservatively screened bulk rows; retain other imports as drafts.

Run from the project root: python manage.py shell < scripts/publish_bulk_pyqs.py
The selected IDs are written to data/bulk_pyq_published_ids.json for audit.
"""
import json
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.content.models import Question, QuestionRevision


root = Path.cwd()
rows = json.loads((root / 'data/bulk_pyq_batch.json').read_text())['questions']
limits = {
    ('comedk', 'Physics'): 70, ('comedk', 'Chemistry'): 60,
    ('comedk', 'Mathematics'): 10,
    ('mht-cet', 'Physics'): 20, ('mht-cet', 'Chemistry'): 35,
    ('cuet-ug', 'Biology'): 30, ('cuet-ug', 'Chemistry'): 25,
    ('cuet-ug', 'General Test'): 30, ('cuet-ug', 'Mathematics'): 8,
    ('cuet-ug', 'Physics'): 1,
}


def eligible(row):
    values = [row['stem']] + [row['option_' + key] for key in 'abcd']
    text = ' '.join(values)
    if any(ord(character) < 32 or character == '\ufffd' for character in text):
        return False
    if any(marker in text for marker in ('Undefined', '\\begin', '<mspace>', 'NaNO/', 'NH_{3})_{4}', '[^{', '::')):
        return False
    if row['stem'].startswith('$') and row['stem'].endswith('$') and len(row['stem']) > 80:
        return False
    if row['exam'] == 'comedk':
        if row['subject'] == 'Chemistry' and any(marker in text for marker in ('[Co', '[Ni', '[Cr', '[Fe', '[Ag', '[Cu')):
            return False
        if row['subject'] == 'Mathematics' and (len(row['stem']) > 220 or any(marker in text for marker in ('∫', 'logx', 'dx', 'dy'))):
            return False
    if row['exam'] == 'mht-cet':
        if any(marker in text for marker in ('1 2', '2 3', 'σ', 'ε0', 'O−', 'R ex', 'Correct Answer')):
            return False
        if len(row['stem']) > 200:
            return False
    if row['exam'] == 'cuet-ug' and row['subject'] == 'Mathematics':
        if any(marker in text for marker in ('dx', 'dy', 'sin2', 'cos2', '', '')):
            return False
    return all(value.count('$') % 2 == 0 for value in values)


def rank(row):
    text = row['stem'] + ' '.join(row['option_' + key] for key in 'abcd')
    return (text.count('$') * 2 + len(row['stem']) // 90 + sum(len(row['option_' + key]) > 65 for key in 'abcd'),
            len(row['stem']), row['source_id'])


grouped = defaultdict(list)
for row in rows:
    if eligible(row):
        grouped[(row['exam'], row['subject'])].append(row)

selected = []
for group, limit in limits.items():
    if len(grouped[group]) < limit:
        raise RuntimeError(f'Only {len(grouped[group])} eligible rows for {group}; expected {limit}.')
    selected.extend(sorted(grouped[group], key=rank)[:limit])

# Compare against the already published bank and against this batch. Different
# source IDs can describe the same paper question in different words.
existing = defaultdict(list)
for exam, subject, stem in Question.objects.filter(kind='PYQ', status='published').exclude(
        section__startswith='Excel · bulk-').values_list(
        'paper__exam__slug', 'topic__chapter__subject__name', 'stem'):
    existing[(exam, subject)].append(stem.casefold())

ready = []
for row in selected:
    key = row['exam'], row['subject']
    stem = row['stem'].casefold()
    if any(SequenceMatcher(None, stem, other).ratio() > 0.88 for other in existing[key]):
        continue
    ready.append(row)
    existing[key].append(stem)

actor = get_user_model().objects.filter(is_superuser=True, is_active=True).first()
if not actor:
    raise RuntimeError('An active platform admin is required.')

newly_published = 0
with transaction.atomic():
    for row in ready:
        question = Question.objects.get(section='Excel · ' + row['source_id'], source=row['source'])
        if question.status == Question.Status.PUBLISHED:
            continue
        if question.status != Question.Status.DRAFT:
            raise RuntimeError(f'Unexpected editorial status for {row["source_id"]}: {question.status}')
        before = {'status': question.status, 'version': question.version,
                  'difficulty_basis': question.difficulty_basis, 'taxonomy_basis': question.taxonomy_basis}
        question.status = Question.Status.PUBLISHED
        question.version += 1
        question.difficulty_basis = 'Initial bulk rating; refine in admin after subject review.'
        question.taxonomy_basis = 'Paper-level grouping; refine chapter and topic in admin.'
        question.full_clean()
        question.save(update_fields=['status', 'version', 'difficulty_basis', 'taxonomy_basis', 'updated_at'])
        QuestionRevision.objects.create(question=question, version=before['version'], snapshot=before, edited_by=actor)
        AuditEvent.objects.create(tenant=None, actor=actor, action='question_saved', object_type='Question',
                                  object_id=str(question.pk), before=before,
                                  after={'status': question.status, 'version': question.version})
        newly_published += 1

manifest = {'source_ids': [row['source_id'] for row in ready], 'notes':
            'Published after structural screening and near-duplicate checks. Publisher answer keys were not independently verified for every item.'}
(root / 'data/bulk_pyq_published_ids.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(f'{len(ready)} structurally screened questions selected; {newly_published} newly published.')
