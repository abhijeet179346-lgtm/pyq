import re
from django.db.models import Q
from apps.tenancy.features import is_enabled
from apps.features.models import TenantExam
from .models import MaterialSection


def recommend_sections(tenant, topic_id=None, chapter=None, subject=None, stem='', question_id=None):
    if not is_enabled(tenant,'study_material'):return []
    enabled=TenantExam.objects.filter(tenant=tenant,enabled=True,exam__active=True).values_list('exam_id',flat=True)
    visible=MaterialSection.objects.filter(active=True,document__active=True).filter(Q(document__tenant=tenant)|Q(document__tenant__isnull=True)).filter(Q(document__exams__isnull=True)|Q(document__exams__in=enabled)).select_related('document','topic__chapter__subject').distinct()
    candidates=[]
    if question_id:
        candidates.extend((section,'Teacher-linked section',1000) for section in visible.filter(question_links__question_id=question_id))
    if topic_id:candidates.extend((section,'Same topic',600) for section in visible.filter(topic_id=topic_id)[:20])
    if chapter and subject:candidates.extend((section,'Same chapter',400) for section in visible.filter(topic__chapter__name__iexact=chapter,topic__chapter__subject__name__iexact=subject)[:30])
    if subject:candidates.extend((section,'Related subject section',100) for section in visible.filter(topic__chapter__subject__name__iexact=subject)[:100])
    words={word for word in re.findall(r'[a-z]{4,}',stem.casefold()) if word not in {'which','following','correct','question','given','value','what','then','with','that','from','this','have','will'}}
    results={}
    for section,reason,base in candidates:
        content=section.body.casefold();overlap=sum(word in content for word in words)
        if base==100 and not overlap:continue
        score=base+min(overlap,50)
        if section.pk in results and results[section.pk]['score']>=score:continue
        location=next((content.find(word) for word in words if content.find(word)>=0),0)
        start=max(0,location-80)
        results[section.pk]={'section':section,'reason':reason,'score':score,'excerpt':section.body[start:start+360]}
    return sorted(results.values(),key=lambda item:(-item['score'],item['section'].pk))[:3]
