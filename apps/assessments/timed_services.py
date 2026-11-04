import json
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.learning.answering import normalize_answer


def validate_questions(rows):
    if not isinstance(rows,list) or not 1 <= len(rows) <= 250:raise ValidationError('A paper must have 1–250 questions.')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or not all(row.get(key) for key in ['key','stem','subject','question_type']) or row.get('answer') in (None,'',[]):raise ValidationError('Question key, stem, answer, subject and type are required.')
        if not isinstance(row['key'],str) or len(row['key'])>100 or not row['key'].isascii() or not all(character.isalnum() or character in '-_' for character in row['key']):raise ValidationError('Question keys must use at most 100 ASCII letters, numbers, hyphens or underscores.')
        if row['key'] in seen:raise ValidationError('Duplicate question key within a test.')
        seen.add(row['key'])
        if row['question_type'] not in ['single_correct','multi_correct','integer','numerical']:raise ValidationError('Unsupported test question type.')
        marking=row.get('marking',{})
        for name in ['correct','incorrect','unanswered']:
            try:
                number=Decimal(str(marking[name]))
                if not number.is_finite() or abs(number)>100:raise ValueError()
            except (KeyError,InvalidOperation,ValueError):raise ValidationError('Invalid marking scheme.')
        if row['question_type'] in ['integer','numerical']:
            try:
                if not Decimal(str(row['answer'])).is_finite():raise ValueError()
            except (InvalidOperation,ValueError):raise ValidationError('Numerical answer must be a finite number.')
        options=row.get('options',[])
        if not isinstance(options,list):raise ValidationError('Options must be a list.')
        if row['question_type'] in ['single_correct','multi_correct']:
            keys=[str(option.get('id','')) for option in options if isinstance(option,dict)]
            answers=row['answer'] if isinstance(row['answer'],list) else [row['answer']]
            if any(not isinstance(answer,str) for answer in answers):raise ValidationError('Choice answers must be option IDs as text.')
            if len(keys)<2 or len(set(keys))!=len(keys) or not set(answers).issubset(keys):raise ValidationError('Answer key does not match options.')


def numeric_equal(a,b):
    try:
        left,right=Decimal(str(a).strip()),Decimal(str(b).strip())
        return left.is_finite() and right.is_finite() and left==right
    except (InvalidOperation,ValueError):return False


def grade(row, selected):
    marking=row['marking']; expected=row['answer'] if isinstance(row['answer'],list) else [row['answer']]
    if not selected or not any(str(value).strip() for value in selected):return Decimal(str(marking.get('unanswered',0))),'unanswered'
    chosen={normalize_answer(x) for x in selected}; correct={normalize_answer(x) for x in expected}
    exact=chosen==correct
    if row['question_type'] in ['integer','numerical'] and len(selected)==1 and len(expected)==1:exact=numeric_equal(selected[0],expected[0]) or exact
    if exact:return Decimal(str(marking['correct'])),'correct'
    if marking.get('partial') and row['question_type']=='multi_correct' and chosen and chosen < correct:
        return Decimal(str(min(len(chosen),float(marking['correct'])))),'partial'
    return Decimal(str(marking['incorrect'])),'incorrect'


def finish(attempt, expired=False):
    if attempt.status!='in_progress':return attempt
    rows=[];total=Decimal('0')
    for question in attempt.snapshot:
        selected=attempt.answers.get(question['key'],[])
        score,state=grade(question,selected);total+=score
        rows.append({'key':question['key'],'score':str(score),'state':state})
    attempt.result={'questions':rows};attempt.score=total
    attempt.status='expired' if expired or timezone.now()>=attempt.deadline else 'submitted'
    attempt.submitted_at=timezone.now();attempt.save(update_fields=['result','score','status','submitted_at'])
    return attempt
