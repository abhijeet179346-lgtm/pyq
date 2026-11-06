"""Resolve an explicit practice selection against visible published questions."""
from django.db.models import Q
from django.http import Http404
from apps.catalog.browsing import taxonomy_filters


def selection(visible, params, allowed_subjects=None):
    queryset, taxonomy = taxonomy_filters(visible, params, allowed_subjects)
    tree = {}
    for row in queryset.values('id', 'topic__chapter_id', 'topic__chapter__name', 'topic_id', 'topic__name', 'subtopic_id', 'subtopic__name'):
        if row['topic__chapter_id'] is None:
            continue
        chapter = tree.setdefault(str(row['topic__chapter_id']), {'key': str(row['topic__chapter_id']), 'name': row['topic__chapter__name'], 'count': 0, 'topics': {}})
        chapter['count'] += 1
        topic = chapter['topics'].setdefault(str(row['topic_id']), {'key': str(row['topic_id']), 'name': row['topic__name'], 'count': 0, 'subtopics': {}})
        topic['count'] += 1
        if row['subtopic_id']:
            leaf = topic['subtopics'].setdefault(str(row['subtopic_id']), {'key': str(row['subtopic_id']), 'name': row['subtopic__name'], 'count': 0})
            leaf['count'] += 1
    chosen = {}
    filters = Q(pk__in=[])
    for name, field in [('chapters', 'topic__chapter_id'), ('topics', 'topic_id'), ('subtopics', 'subtopic_id')]:
        values = params.getlist(name)
        if any(not (value.isascii() and value.isdecimal() and len(value) <= 18) for value in values):
            raise Http404('Invalid selection.')
        values = set(str(int(value)) for value in values)
        allowed = {str(value) for value in queryset.values_list(field, flat=True) if value is not None}
        if not values.issubset(allowed):
            raise Http404('This selection is unavailable in the chosen exam and subject.')
        chosen['selection_' + name] = values
        if values:
            filters |= Q(**{field + '__in': [int(value) for value in values]})
    scope_count = queryset.count()
    if any(chosen.values()):
        queryset = queryset.filter(filters)
    difficulty=params.get('difficulty', '')
    levels={'easy':[1,2], 'moderate':[3], 'hard':[4,5]}
    if difficulty:
        if difficulty not in levels:raise Http404('Invalid difficulty.')
        queryset=queryset.filter(difficulty__in=levels[difficulty])
    question_type=params.get('question_type','')
    if question_type:
        from apps.content.models import Question
        if question_type not in Question.Type.values:raise Http404('Invalid question type.')
        queryset=queryset.filter(question_type=question_type)
    year, search = (params.get(key, '').strip() for key in ('year', 'q'))
    if year:
        if not (year.isascii() and year.isdecimal() and len(year) == 4):
            raise Http404('Invalid year.')
        queryset = queryset.filter(paper__year=int(year))
    if search:
        queryset = queryset.filter(Q(stem__icontains=search) | Q(topic__name__icontains=search) | Q(subtopic__name__icontains=search))
    chapters = sorted(tree.values(), key=lambda item: item['name'])
    for chapter in chapters:
        chapter['topics'] = sorted(chapter['topics'].values(), key=lambda item: item['name'])
        for topic in chapter['topics']:
            topic['subtopics'] = sorted(topic['subtopics'].values(), key=lambda item: item['name'])
    return queryset, {**taxonomy, **chosen, 'selection_tree': chapters, 'scope_count': scope_count,
        'selected_difficulty':difficulty, 'selected_question_type':question_type, 'selected_year': year, 'search': search, 'has_selection': any(chosen.values())}
