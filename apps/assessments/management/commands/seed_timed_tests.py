import json,re,zipfile,hashlib
from pathlib import Path
from collections import defaultdict
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from apps.catalog.models import Exam
from apps.tenancy.models import Tenant
from apps.features.models import FeatureEntitlement
from apps.assessments.models import TimedTest


class Command(BaseCommand):
    help='Install five two-paper JEE Advanced mock sets and five subject tests from the pinned question snapshot.'
    def add_arguments(self,parser):
        parser.add_argument('--refresh',action='store_true',help='Refresh seeded test content only when no attempt exists.')
    @transaction.atomic
    def handle(self,*args,**options):
        data=json.loads((settings.BASE_DIR/'data/timed_test_questions.json').read_text())
        tenant=Tenant.objects.get(is_master=True);exam=Exam.objects.get(slug='jee-advanced')
        FeatureEntitlement.objects.get_or_create(tenant=tenant,key='tests',defaults={'entitled':True,'enabled_override':True})
        from apps.content.models import Question
        bank={re.sub(r'\s+','',q.stem).casefold():q for q in Question.objects.select_related('topic__chapter__subject','subtopic')}
        for row in data:
            item=bank.get(re.sub(r'\s+','',row['stem']).casefold())
            if item:
                row.update(question_id=item.pk,topic_id=item.topic_id,chapter=item.topic.chapter.name,topic=item.topic.name,subtopic=item.subtopic.name if item.subtopic_id else '')
        pools=defaultdict(list)
        for question in data:pools[(question['subject'],question['question_type'])].append(question)
        cursor=defaultdict(int)
        def choose(subject,kind,count,used):
            pool=pools[(subject,kind)];result=[]
            for _ in range(len(pool)*2):
                row=pool[cursor[(subject,kind)]%len(pool)];cursor[(subject,kind)]+=1
                if row['key'] in used:continue
                used.add(row['key']);result.append(row)
                if len(result)==count:return result
            raise CommandError(f'Insufficient unique {subject} {kind} questions.')
        created=0
        for number in range(1,6):
            used=set()
            for paper in [1,2]:
                rows=[]
                for subject in ['Physics','Chemistry','Mathematics']:
                    for kind in ['single_correct','multi_correct','numerical']:
                        rows.extend(choose(subject,kind,6,used))
                obj,new=TimedTest.objects.get_or_create(tenant=tenant,name=f'JEE Advanced Mock {number} · Paper {paper}',defaults={'exam':exam,'series':f'Full mock {number}','paper_number':paper,'duration_minutes':180,'active':True,'source':'https://github.com/dair-iitd/jeebench','questions':rows,'instructions':'54 questions, 180 marks, 180 minutes. Each subject: 6 single-correct (+3/−1), 6 multiple-correct (+4/−2, +1 per correctly selected option for a proper subset), and 6 numerical (+3/0). Unanswered: 0. Both papers comprise one full mock set. This is an Advanced-style compilation, not a reproduction of an official yearly paper. Questions can recur across different mock sets but not within a two-paper set.'})
                if not new and options['refresh'] and not obj.timed_attempts.exists():obj.questions=rows;obj.save(update_fields=['questions'])
                created+=new
        for index,subject in enumerate(['Physics','Chemistry','Mathematics','Physics','Chemistry'],1):
            used=set();rows=[]
            for kind in ['single_correct','multi_correct','numerical']:rows.extend(choose(subject,kind,6,used))
            obj,new=TimedTest.objects.get_or_create(tenant=tenant,name=f'{subject} Subject Test {index}',defaults={'exam':exam,'subject':subject,'series':'Subject practice','duration_minutes':60,'questions':rows,'active':True,'source':'https://github.com/dair-iitd/jeebench','instructions':'18 questions, 60 marks, 60 minutes. Single correct +3/−1; multiple correct +4/−2 with partial credit; numerical +3/0. Unanswered: 0. Sourced past questions compiled for subject practice.'})
            if not new and options['refresh'] and not obj.timed_attempts.exists():obj.questions=rows;obj.save(update_fields=['questions'])
            created+=new
        self.stdout.write(f'{created} papers created (10 full-mock papers + 5 subject tests total).')
