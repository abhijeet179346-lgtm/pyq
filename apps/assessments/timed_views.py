from datetime import timedelta
from collections import Counter
import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse,Http404,JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from apps.portal.direct import _direct_user, exam_cards, questions
from apps.features.models import TenantExam
from apps.materials.recommendations import recommend_sections
from .models import TimedTest,TimedAttempt
from .timed_services import finish


def snapshot_with_figures(rows):
    """Copy each linked question's images into the attempt snapshot so later edits never change a started paper."""
    from apps.content.models import Question
    ids=[row.get('question_id') for row in rows if row.get('question_id') and not row.get('figures')]
    paths=dict(Question.objects.filter(pk__in=ids).values_list('pk','figure_paths')) if ids else {}
    return [{**row,'figures':row.get('figures') or paths.get(row.get('question_id')) or []} for row in rows]


def row_figures(attempt,row):
    return [reverse('timed_figure',args=[attempt.pk,row['key'],index]) for index in range(len(row.get('figures') or []))]


EVENT_TYPES={'fullscreen_exit','tab_hidden'}
TYPE_LABELS={'single_correct':'Single correct','multi_correct':'Multiple correct','integer':'Integer','numerical':'Numerical'}


def paper_sections(rows):
    """Subjects in paper order, each with question count and the question types / marking used."""
    sections={}
    for row in rows:
        section=sections.setdefault(row['subject'],{'subject':row['subject'],'count':0,'types':{}})
        section['count']+=1
        kind=section['types'].setdefault(row['question_type'],{'label':TYPE_LABELS.get(row['question_type'],row['question_type']),'count':0,'marking':row.get('marking',{})})
        kind['count']+=1
    return [{**item,'types':list(item['types'].values())} for item in sections.values()]


def violations(attempt):
    return sum(1 for event in attempt.events if event.get('type') in EVENT_TYPES)


def record_tracking(item,post):
    """Merge visited keys, per-question seconds and proctoring events sent by the exam page."""
    keys={row['key'] for row in item.snapshot}
    visited=[key for key in post.get('visited','').split(',') if key in keys]
    item.visited=list(dict.fromkeys([*item.visited,*visited]))
    try:spent=json.loads(post.get('time_spent') or '{}')
    except ValueError:spent={}
    limit=item.test.duration_minutes*60
    if isinstance(spent,dict):
        for key,value in spent.items():
            if key in keys and isinstance(value,(int,float)):item.time_spent[key]=max(0,min(int(value),limit))
    try:events=json.loads(post.get('events') or '[]')
    except ValueError:events=[]
    if isinstance(events,list):
        now=timezone.now().isoformat()
        for event in events[:50]:
            if isinstance(event,dict) and event.get('type') in EVENT_TYPES and len(item.events)<500:
                item.events.append({'type':event['type'],'at':now})


def scope(request):
    if getattr(request,'is_core_domain',False):tenant=_direct_user(request,'tests')
    else:
        from apps.learning.views import _tenant_user
        from apps.portal.services import workspace_ready
        from apps.tenancy.features import is_enabled
        tenant=_tenant_user(request)
        if not workspace_ready(request.user,tenant) or not is_enabled(tenant,'tests'):raise PermissionDenied('Tests require an active enrolled account and enabled test access.')
    exams=TenantExam.objects.filter(tenant=tenant,enabled=True,exam__active=True).values_list('exam_id',flat=True)
    from django.db.models import Q
    # Learner-built custom tests are private to their owner.
    return tenant,TimedTest.objects.filter(tenant=tenant,exam_id__in=exams,active=True).filter(Q(owner__isnull=True)|Q(owner=request.user))


@login_required
@require_GET
def catalog(request):
    tenant,tests=scope(request)
    for expired in TimedAttempt.objects.filter(user=request.user,test__in=tests,status='in_progress',deadline__lte=timezone.now()):
        with transaction.atomic():
            expired=TimedAttempt.objects.select_for_update().get(pk=expired.pk)
            finish(expired,expired=True)
    exams = [exam for exam in exam_cards(tenant, request.user) if exam.pk != 'practice' and exam.question_count]
    exam_id = request.GET.get('exam', '')
    selected = next((exam for exam in exams if str(exam.pk) == exam_id), None)
    if exam_id and selected is None:
        raise Http404('This exam has no available questions.')
    subjects = []
    if selected:
        from django.db.models import Count, Q
        visible = questions(tenant, request.user).filter(Q(paper__exam=selected) | Q(exam=selected))
        subjects = list(visible.values('topic__chapter__subject__name').annotate(total=Count('id')).order_by('topic__chapter__subject__name'))
    attempts = TimedAttempt.objects.filter(user=request.user, test__in=tests).select_related('test', 'test__exam').order_by('-started_at')
    drafts = tests.filter(owner=request.user, timed_attempts__isnull=True).order_by('-created_at')
    return render(request, 'assessments/timed_catalog.html', {
        'exams': exams, 'selected_exam': selected, 'subjects': subjects,
        'in_progress': attempts.filter(status='in_progress'),
        'recent_results': attempts.exclude(status='in_progress'), 'drafts': drafts,
    })


@login_required
@require_POST
def generate(request):
    from django.core.exceptions import ValidationError
    from django.db.models import Q
    from apps.content.models import Question
    from .custom_tests import PATTERNS, DEFAULT, build_custom_test, snapshot_row
    from .timed_services import validate_questions

    tenant, _ = scope(request)
    exam_id = request.POST.get('exam', '')
    exam = next((item for item in exam_cards(tenant, request.user)
                 if item.pk != 'practice' and str(item.pk) == exam_id and item.question_count), None)
    if exam is None:
        raise Http404('This exam has no available questions.')
    visible = questions(tenant, request.user).filter(Q(paper__exam=exam) | Q(exam=exam)).filter(
        question_type__in=(Question.Type.SINGLE, Question.Type.MULTI, Question.Type.INTEGER, Question.Type.NUMERICAL))
    subject = request.POST.get('subject', '').strip()
    names = list(visible.order_by('topic__chapter__subject__name').values_list(
        'topic__chapter__subject__name', flat=True).distinct())
    if subject:
        if subject not in names:
            raise Http404('This subject is unavailable for this exam.')
        names = [subject]
    target, minutes = (25, 60) if subject else (75, 180)
    pattern = PATTERNS.get(exam.slug, DEFAULT)
    # Take an even mix of subjects, filling spare places from subjects with more questions.
    sources = [iter(visible.filter(topic__chapter__subject__name=name).order_by('?').iterator(chunk_size=100)) for name in names]
    picked = []
    while sources and len(picked) < target:
        remaining = []
        for source in sources:
            for item in source:
                try:
                    validate_questions([snapshot_row(item, pattern, exam)])
                except (ValidationError, KeyError, TypeError, ValueError):
                    continue
                picked.append(item)
                remaining.append(source)
                break
            if len(picked) == target:
                break
        sources = remaining
    title = f'{exam.name} · {subject}' if subject else f'{exam.name} · Full test'
    try:
        test, _ = build_custom_test(request.user, tenant, exam, picked, title, minutes)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(f'{reverse("timed_catalog")}?exam={exam.pk}')
    if len(picked) < target:
        messages.info(request, f'This test has {len(picked)} available questions.')
    return redirect('timed_start', test_id=test.pk)


@login_required
@require_http_methods(['GET','POST'])
def start(request,test_id):
    tenant,tests=scope(request);test=get_object_or_404(tests,pk=test_id)
    if request.method=='POST':
        if test.series.startswith('Full mock') and test.paper_number==2:
            first=tests.filter(series=test.series,paper_number=1).first()
            prior=TimedAttempt.objects.filter(test=first,user=request.user).first() if first else None
            if prior and prior.status=='in_progress' and timezone.now()>=prior.deadline:finish(prior,expired=True)
            if not prior or prior.status=='in_progress':
                return render(request,'assessments/timed_instructions.html',{'test':test,'count':len(test.questions),'sections':paper_sections(test.questions),'error':'Complete Paper 1 of this mock set before starting Paper 2.'},status=409)
        test.full_clean()
        now=timezone.now()
        with transaction.atomic():
            attempt,_=TimedAttempt.objects.get_or_create(test=test,user=request.user,defaults={'started_at':now,'deadline':now+timedelta(minutes=test.duration_minutes),'snapshot':snapshot_with_figures(test.questions)})
        return redirect('timed_attempt',attempt_id=attempt.pk)
    existing=TimedAttempt.objects.filter(test=test,user=request.user,status='in_progress').first()
    return render(request,'assessments/timed_instructions.html',{'test':test,'count':len(test.questions),'sections':paper_sections(test.questions),'resuming':existing is not None})


@login_required
@require_http_methods(['GET','POST'])
def attempt(request,attempt_id):
    tenant,tests=scope(request)
    with transaction.atomic():
        item=get_object_or_404(TimedAttempt.objects.select_for_update().select_related('test'),pk=attempt_id,user=request.user,test__in=tests)
        if item.status=='in_progress' and timezone.now()>=item.deadline:finish(item,expired=True)
        if item.status!='in_progress':
            if request.method=='POST' and request.POST.get('action')=='save':return JsonResponse({'result_url':redirect('timed_result',attempt_id=item.pk).url})
            return redirect('timed_result',attempt_id=item.pk)
        if request.method=='POST':
            answers={};review=[]
            for row in item.snapshot:
                values=[value.strip()[:1000] for value in request.POST.getlist('answer_'+row['key'])[:10] if value.strip()]
                if row['options']:
                    allowed={str(option['id']) for option in row['options']}
                    if not set(values).issubset(allowed):raise PermissionDenied('Invalid answer option.')
                if values:answers[row['key']]=values
                if request.POST.get('review_'+row['key']):review.append(row['key'])
            item.answers=answers;item.review=review
            record_tracking(item,request.POST)
            item.save(update_fields=['answers','review','visited','time_spent','events'])
            limit=item.test.fullscreen_exit_limit
            if limit and violations(item)>=limit:
                finish(item);item.result['ended_by']='proctoring';item.save(update_fields=['result'])
                if request.POST.get('action')=='save':return JsonResponse({'result_url':redirect('timed_result',attempt_id=item.pk).url,'ended_by':'proctoring'})
                return redirect('timed_result',attempt_id=item.pk)
            if request.POST.get('action')=='submit':
                finish(item);return redirect('timed_result',attempt_id=item.pk)
            return JsonResponse({'state':'saved','server_time':timezone.now().isoformat(),'violations':violations(item)})
        rows=[]
        for index,row in enumerate(item.snapshot,1):
            # Keep answer keys/solutions out of the response until the deadline/submission.
            rows.append({'key':row['key'],'stem':row['stem'],'subject':row['subject'],'section':row.get('section',''),
                'question_type':row['question_type'],'type_label':TYPE_LABELS.get(row['question_type'],row['question_type']),'options':row['options'],'marking':row['marking'],'number':index,
                'saved':item.answers.get(row['key'],[]),'review':row['key'] in item.review,'figure_urls':row_figures(item,row)})
    return render(request,'assessments/timed_attempt.html',{'attempt':item,'test':item.test,'rows':rows,'sections':paper_sections(item.snapshot),
        'server_time':timezone.now().isoformat(),'exam_state':{'visited':item.visited,'time_spent':item.time_spent,'violations':violations(item),
        'limit':item.test.fullscreen_exit_limit}})


STATE_LABELS={'correct':'Correct','partial':'Partly correct','incorrect':'Wrong','unanswered':'Not answered'}


def analyse(item):
    """Section-wise statistics, time use and per-question review rows for a finished attempt."""
    marks={row['key']:row for row in item.result.get('questions',[])}
    rows=[];sections={}
    for number,question in enumerate(item.snapshot,1):
        mark=marks.get(question['key'],{});state=mark.get('state','unanswered')
        selected=item.answers.get(question['key'],[])
        expected=question['answer'] if isinstance(question['answer'],list) else [question['answer']]
        options=[{**option,'is_answer':str(option['id']) in [str(x) for x in expected],'is_selected':str(option['id']) in selected} for option in question.get('options',[])]
        seconds=int(item.time_spent.get(question['key'],0))
        rows.append({'number':number,'question':question,'mark':mark,'state':state,'state_label':STATE_LABELS.get(state,state),
            'score':float(mark.get('score',0)),'selected':selected,'expected':expected,'options':options,'seconds':seconds,
            'marked':question['key'] in item.review,'figure_urls':row_figures(item,question)})
        section=sections.setdefault(question['subject'],{'subject':question['subject'],'questions':0,'correct':0,'partial':0,'incorrect':0,'unanswered':0,'score':0.0,'max':0.0,'seconds':0})
        section['questions']+=1;section[state if state in section else 'unanswered']+=1
        section['score']+=float(mark.get('score',0));section['max']+=float(question.get('marking',{}).get('correct',0));section['seconds']+=seconds
    for section in sections.values():
        attempted=section['questions']-section['unanswered']
        section['attempted']=attempted
        section['accuracy']=round(100*(section['correct']+0.5*section['partial'])/attempted) if attempted else None
    total={key:sum(section[key] for section in sections.values()) for key in ('questions','correct','partial','incorrect','unanswered','score','max','seconds','attempted')}
    total['accuracy']=round(100*(total['correct']+0.5*total['partial'])/total['attempted']) if total['attempted'] else None
    timed=[row for row in rows if row['seconds']]
    def average(states):
        values=[row['seconds'] for row in timed if row['state'] in states]
        return round(sum(values)/len(values)) if values else None
    time={'used':total['seconds'],'limit':item.test.duration_minutes*60,'avg':round(total['seconds']/len(timed)) if timed else None,
          'avg_correct':average({'correct','partial'}),'avg_wrong':average({'incorrect'}),
          'slowest':sorted(timed,key=lambda row:-row['seconds'])[:5]}
    exits=sum(1 for event in item.events if event.get('type')=='fullscreen_exit')
    proctoring={'exits':exits,'tabs':sum(1 for event in item.events if event.get('type')=='tab_hidden'),'ended_by':item.result.get('ended_by')}
    counts=Counter(row['state'] for row in rows)
    filters=[('all','All',len(rows)),('incorrect','Wrong',counts['incorrect']),('partial','Partly correct',counts['partial']),
             ('unanswered','Not answered',counts['unanswered']),('correct','Correct',counts['correct']),('marked','Marked for review',sum(row['marked'] for row in rows))]
    return {'rows':rows,'sections':list(sections.values()),'total':total,'time':time,'proctoring':proctoring,'filters':[f for f in filters if f[0]=='all' or f[2]]}


@login_required
@require_GET
def result(request,attempt_id):
    tenant,tests=scope(request)
    with transaction.atomic():
        item=get_object_or_404(TimedAttempt.objects.select_for_update().select_related('test','test__exam'),pk=attempt_id,user=request.user,test__in=tests)
        if item.status=='in_progress' and timezone.now()>=item.deadline:finish(item,expired=True)
        if item.status=='in_progress':return redirect('timed_attempt',attempt_id=item.pk)
    context=analyse(item)
    for row in context['rows']:
        question=row['question'];row['recommendations']=[]
        if row['state'] in ['incorrect','partial','unanswered']:
            row['recommendations']=recommend_sections(tenant,question.get('topic_id'),question.get('chapter'),question.get('subject'),question.get('stem',''),question.get('question_id'))
        if question.get('topic_id') and getattr(request,'is_core_domain',False):
            row['practice_url']=reverse('direct_bank')+f"?exam={item.test.exam_id}&topic={question['topic_id']}&view=browse"
    return render(request,'assessments/timed_result.html',{'attempt':item,'test':item.test,**context,
        'states':dict(Counter(row['state'] for row in context['rows']))})


@login_required
@require_GET
def figure(request,attempt_id,key,index):
    from apps.content.figures import content_type,open_figure
    tenant,tests=scope(request)
    item=get_object_or_404(TimedAttempt,pk=attempt_id,user=request.user,test__in=tests)
    row=next((row for row in item.snapshot if row.get('key')==key),None)
    figures=(row or {}).get('figures') or []
    if not 0<=index<len(figures):raise Http404()
    handle=open_figure(figures[index])
    if handle is None:raise Http404()
    response=FileResponse(handle,content_type=content_type(figures[index]));response['Cache-Control']='private, max-age=3600'
    return response
