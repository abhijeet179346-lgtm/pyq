from datetime import timedelta
from collections import Counter
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse,Http404,JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from apps.portal.direct import _direct_user
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


def scope(request):
    if getattr(request,'is_core_domain',False):tenant=_direct_user(request,'tests')
    else:
        from apps.learning.views import _tenant_user
        from apps.portal.services import workspace_ready
        from apps.tenancy.features import is_enabled
        tenant=_tenant_user(request)
        if not workspace_ready(request.user,tenant) or not is_enabled(tenant,'tests'):raise PermissionDenied('Tests require an active enrolled account and enabled test access.')
    exams=TenantExam.objects.filter(tenant=tenant,enabled=True,exam__active=True).values_list('exam_id',flat=True)
    return tenant,TimedTest.objects.filter(tenant=tenant,exam_id__in=exams,active=True)


@login_required
@require_GET
def catalog(request):
    tenant,tests=scope(request)
    for expired in TimedAttempt.objects.filter(user=request.user,test__in=tests,status='in_progress',deadline__lte=timezone.now()):
        with transaction.atomic():
            expired=TimedAttempt.objects.select_for_update().get(pk=expired.pk)
            finish(expired,expired=True)
    attempts={item.test_id:item for item in TimedAttempt.objects.filter(user=request.user,test__in=tests)}
    rows=[]
    for test in tests:
        rows.append({'test':test,'count':len(test.questions),'attempt':attempts.get(test.pk)})
    summaries=[]
    for series in sorted({row['test'].series for row in rows if row['test'].series.startswith('Full mock')}):
        parts=[row for row in rows if row['test'].series==series]
        finished=[row['attempt'] for row in parts if row['attempt'] and row['attempt'].status!='in_progress']
        summaries.append({'series':series,'completed':len(finished),'papers':len(parts),'score':sum(item.score for item in finished)})
    return render(request,'assessments/timed_catalog.html',{'test_rows':rows,'series_summaries':summaries})


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
                return render(request,'assessments/timed_instructions.html',{'test':test,'count':len(test.questions),'error':'Complete Paper 1 of this mock set before starting Paper 2.'},status=409)
        test.full_clean()
        now=timezone.now()
        with transaction.atomic():
            attempt,_=TimedAttempt.objects.get_or_create(test=test,user=request.user,defaults={'started_at':now,'deadline':now+timedelta(minutes=test.duration_minutes),'snapshot':snapshot_with_figures(test.questions)})
        return redirect('timed_attempt',attempt_id=attempt.pk)
    return render(request,'assessments/timed_instructions.html',{'test':test,'count':len(test.questions)})


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
            item.answers=answers;item.review=review;item.save(update_fields=['answers','review'])
            if request.POST.get('action')=='submit':
                finish(item);return redirect('timed_result',attempt_id=item.pk)
            return JsonResponse({'state':'saved','server_time':timezone.now().isoformat()})
        rows=[]
        for index,row in enumerate(item.snapshot,1):
            # Keep answer keys/solutions out of the response until the deadline/submission.
            rows.append({'key':row['key'],'stem':row['stem'],'subject':row['subject'],'section':row.get('section',''),
                'question_type':row['question_type'],'options':row['options'],'marking':row['marking'],'number':index,
                'saved':item.answers.get(row['key'],[]),'review':row['key'] in item.review,'figure_urls':row_figures(item,row)})
    return render(request,'assessments/timed_attempt.html',{'attempt':item,'test':item.test,'rows':rows,'server_time':timezone.now().isoformat()})


@login_required
@require_GET
def result(request,attempt_id):
    tenant,tests=scope(request)
    with transaction.atomic():
        item=get_object_or_404(TimedAttempt.objects.select_for_update().select_related('test'),pk=attempt_id,user=request.user,test__in=tests)
        if item.status=='in_progress' and timezone.now()>=item.deadline:finish(item,expired=True)
        if item.status=='in_progress':return redirect('timed_attempt',attempt_id=item.pk)
    marks={row['key']:row for row in item.result.get('questions',[])};rows=[]
    for question in item.snapshot:
        mark=marks.get(question['key'],{})
        recommendations=[]
        if mark.get('state') in ['incorrect','partial','unanswered']:
            recommendations=recommend_sections(tenant,question.get('topic_id'),question.get('chapter'),question.get('subject'),question.get('stem',''),question.get('question_id'))
        rows.append({'question':question,'mark':mark,'selected':item.answers.get(question['key'],[]),'recommendations':recommendations,'figure_urls':row_figures(item,question)})
    return render(request,'assessments/timed_result.html',{'attempt':item,'test':item.test,'rows':rows,'states':dict(Counter(row['state'] for row in item.result.get('questions',[])))})


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
