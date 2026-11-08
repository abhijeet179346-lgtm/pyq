import difflib,json,hashlib,re
from decimal import Decimal
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.utils.text import slugify
from apps.catalog.models import Exam,Subject,Chapter,Topic,Subtopic,Paper
from apps.content.models import Question,ConceptTag,QuestionConceptTag
from apps.content.math_text import repair_text
from apps.assessments.models import TimedTest
from apps.materials.models import MaterialDocument,MaterialSection
from apps.audit.models import AuditEvent


def truth(value):return str(value).strip().casefold() in ['1','true','yes','on']
def required(row,*keys):
    missing=[key for key in keys if not row.get(key)]
    if missing:raise ValueError('Missing '+', '.join(missing))
def taxonomy(row):
    required(row,'subject','chapter','topic')
    subject,_=Subject.objects.get_or_create(name=row['subject'],defaults={'slug':slugify(row['subject'])})
    chapter,_=Chapter.objects.get_or_create(subject=subject,name=row['chapter'])
    topic,_=Topic.objects.get_or_create(chapter=chapter,name=row['topic'])
    subtopic=None
    if row.get('subtopic'):subtopic,_=Subtopic.objects.get_or_create(topic=topic,name=row['subtopic'])
    return topic,subtopic

def exam_for(row):
    """Resolve an existing exam by slug or name. Unknown exams are errors, never created implicitly."""
    required(row,'exam');value=row['exam'].strip();key=slugify(value)
    exam=Exam.objects.filter(slug=key).first() or next((item for item in Exam.objects.all() if slugify(item.name)==key),None)
    if exam:return exam
    slugs=sorted(Exam.objects.values_list('slug',flat=True))
    close=difflib.get_close_matches(key,slugs,n=1,cutoff=0.6)
    hint=f' Did you mean "{close[0]}"?' if close else ''
    raise ValueError(f'Unknown exam "{value}".{hint} Valid exams: {", ".join(slugs) or "none"}. Platform admins add new exams with an Exams import.')

def pyq_destination(job):
    """Global PYQs come only from platform admins uploading for the master tenant.
    Any other exam-targeted upload stays private to the job's tenant."""
    return bool(job.initiated_by and job.initiated_by.is_superuser and job.tenant.is_master)


def exam_enabled_for(tenant, exam):
    from apps.features.models import TenantExam
    return exam.active and TenantExam.objects.filter(tenant=tenant, exam=exam, enabled=True).exists()


def question_row(row):
    required(row,'stem','answer','question_type','subject')
    kind=row['question_type'];options=[{'id':key,'text':row['option_'+key.lower()]} for key in 'ABCD' if row.get('option_'+key.lower())]
    if kind not in ['single_correct','multi_correct','integer','numerical']:raise ValueError('Invalid question_type.')
    answer=row['answer']
    if kind=='multi_correct':
        try:answer=json.loads(answer)
        except ValueError:answer=[part.strip() for part in answer.split(',')]
        if not isinstance(answer,list):raise ValueError('Multiple-correct answers must be a list or comma-separated option IDs.')
    if kind in ['single_correct','multi_correct']:
        keys={option['id'] for option in options};answers=answer if isinstance(answer,list) else [answer]
        if len(keys)<2 or not set(answers).issubset(keys):raise ValueError('Answer does not match option IDs.')
    if kind in ['integer','numerical']:
        if not Decimal(str(answer)).is_finite():raise ValueError('Numerical answer must be finite.')
    return options,answer


def row_images(row,images,preview,prefix):
    """Validate (preview) or store (commit) the images a row names; returns stored paths."""
    from apps.content.figures import MAX_FIGURES,clean_image,store
    from .workbooks import image_names
    names=image_names(row)
    if len(names)>MAX_FIGURES:raise ValueError(f'At most {MAX_FIGURES} images per question.')
    missing=[name for name in names if name not in images]
    if missing:raise ValueError(('Image not found in the zip: ' if images else 'Upload a .zip with the workbook and images to use the images column. Missing: ')+', '.join(missing))
    cleaned=[]
    for name in names:
        try:data,extension=clean_image(images[name],name)
        except ValidationError as exc:raise ValueError(exc.messages[0]) from exc
        cleaned.append((data,extension))
    return [] if preview else [store(data,extension,prefix) for data,extension in cleaned]


def add_warning(row,text):
    if isinstance(row,dict) and text not in row.get('warnings',''):
        row['warnings']=(row.get('warnings','')+' '+text).strip()


def note_duplicate(row,text,original):
    if isinstance(row,dict):
        row['duplicate_of']=str(original)
        add_warning(row,text)


@transaction.atomic
def apply_rows(job,rows,preview=False,images=None):
    images=images or {}
    superuser=bool(job.initiated_by and job.initiated_by.is_superuser)
    if job.kind=='exams' and not superuser:raise PermissionDenied('Only platform administrators may edit exams.')
    errors=[];created=0;tests={};seen={}
    for index,row in enumerate(rows,2):
        source_row=row
        if isinstance(source_row,dict):source_row.pop('duplicate_of',None)
        try:
            with transaction.atomic():
                row = dict(row)
                for field in ('stem', 'solution', 'option_a', 'option_b', 'option_c', 'option_d'):
                    if field in row:
                        row[field] = repair_text(row[field])
                if job.kind == 'questions':
                    if job.target_exam_id:
                        supplied = row.get('exam', '').strip()
                        if supplied and exam_for(row).pk != job.target_exam_id:
                            raise ValueError('Workbook exam does not match the selected destination exam.')
                        if not row.get('year') and job.year:
                            row['year'] = str(job.year)
                        if pyq_destination(job):
                            if row.get('kind', '').strip().upper() not in ('', 'PYQ'):
                                raise ValueError('A selected exam destination requires PYQ rows with a year. Practice rows have no exam paper.')
                            row['kind'] = 'PYQ'
                            row['exam'] = job.target_exam.slug
                        else:
                            # Institute upload: a private question for this tenant, tagged with the exam and year.
                            if not exam_enabled_for(job.tenant, job.target_exam):
                                raise ValueError(f'{job.target_exam.name} is not enabled for {job.tenant}.')
                            row['kind'] = 'PRACTICE'
                            row['exam'] = ''
                            extra = [job.target_exam.name] + ([row['year']] if row.get('year') else [])
                            row['tags'] = ','.join(filter(None, [row.get('tags', '')] + extra))
                    if job.target_subject_id:
                        supplied = row.get('subject', '').strip()
                        if supplied and supplied.casefold() != job.target_subject.name.casefold():
                            raise ValueError('Workbook subject does not match the selected destination subject.')
                        row['subject'] = job.target_subject.name
                    if job.target_exam_id and job.target_subject_id and not job.target_exam.subjects.filter(pk=job.target_subject_id).exists():
                        raise ValueError('The selected subject is not enabled for the selected exam.')
                number=row.get('_row',index)
                if job.kind=='exams':
                    required(row,'exam','name')
                    key=slugify(row['exam']);existed=Exam.objects.filter(slug=key).exists()
                    if not existed:Exam.objects.create(slug=key,name=row['name'],active=truth(row.get('active','false')))
                    # Catalogue import adds entries without silently changing existing records.
                    created+=int(not existed);continue
                if job.kind=='materials':
                    required(row,'document','title','body','source','license');topic,_=taxonomy(row)
                    source=row['source']
                    doc,_=MaterialDocument.objects.get_or_create(tenant=job.tenant,title=row['document'],source=source,defaults={'license':row['license'],'default_topic':topic,'uploaded_by':job.initiated_by})
                    if row.get('exam'):
                        exam = exam_for(row)
                        if not exam.subjects.filter(pk=topic.chapter.subject_id).exists():
                            raise ValueError(f'{topic.chapter.subject} is not enabled for {exam.name}.')
                        doc.exams.add(exam)
                    section,section_created=MaterialSection.objects.get_or_create(document=doc,title=row['title'],topic=topic,page_reference=row.get('page_reference',''),defaults={'body':row['body'],'active':False})
                    created+=int(section_created);continue
                options,answer=question_row(row)
                source=row.get('source') or job.source;license=row.get('license') or job.license
                if not source or not license:raise ValueError('Source and licence are required.')
                if job.kind=='tests':
                    required(row,'test_name','exam','duration_minutes','section')
                    exam=exam_for(row)
                    if not exam.subjects.filter(name=row['subject']).exists():
                        raise ValueError(f'{row["subject"]} is not enabled for {exam.name}.')
                    title=row['test_name'];group=tests.setdefault(title,{'row':row,'exam':exam,'questions':[]})
                    if row['exam']!=group['row']['exam'] or row['duration_minutes']!=group['row']['duration_minutes']:raise ValueError('Inconsistent exam or duration within one test.')
                    figures=row_images(row,images,preview,f'job-{job.pk}')
                    group['questions'].append({'key':row.get('source_id') or f'excel-{number}','stem':row['stem'],'figures':figures,'options':options,'answer':answer,'question_type':row['question_type'],'subject':row['subject'],'chapter':row.get('chapter',''),'topic':row.get('topic',''),'subtopic':row.get('subtopic',''),'section':row['section'],'source':source,'license':license,'solution':row.get('solution',''),'marking':{'correct':float(row.get('correct_marks') or 3),'incorrect':float(row.get('incorrect_marks') or 0),'unanswered':float(row.get('unanswered_marks') or 0),'partial':truth(row.get('partial_credit'))}})
                    continue
                topic,subtopic=taxonomy(row)
                kind=(row.get('kind') or 'PRACTICE').upper()
                if kind not in Question.Kind.values:raise ValueError('kind must be PRACTICE or PYQ.')
                paper=None
                if kind=='PYQ':
                    if not superuser:raise ValueError('Only a platform administrator may import global PYQs.')
                    required(row,'exam','year');exam=exam_for(row)
                    if not exam.subjects.filter(name=row['subject']).exists():
                        raise ValueError(f'{row["subject"]} is not enabled for {exam.name}. Change the exam subjects in admin.')
                    year=int(row['year'])
                    if not 1900<=year<=2100:raise ValueError('Invalid exam year.')
                    paper,_=Paper.objects.get_or_create(exam=exam,year=year,sitting=row.get('paper',''))
                source_id=row.get('source_id') or hashlib.sha256(row['stem'].encode()).hexdigest()[:16]
                section='Excel · '+source_id[:70]
                owner=None if kind=='PYQ' else job.tenant
                if Question.objects.filter(tenant=owner,paper=paper,source=source,section=section).exists():continue
                tags=[value.strip() for value in row.get('tags','').split(',') if value.strip()]
                source_page=int(row['source_page']) if row.get('source_page') else None
                question=Question(tenant=owner,kind=kind,paper=paper,section=section,stem=row['stem'],options=options,answer=json.dumps(answer) if isinstance(answer,list) else answer,question_type=row['question_type'],topic=topic,subtopic=subtopic,difficulty=int(row.get('difficulty') or 3),source=source,license=license,status='draft',created_by=job.initiated_by,solution=row.get('solution',''),source_page=source_page,tags=tags+[topic.chapter.subject.name,topic.chapter.name,topic.name]+([subtopic.name] if subtopic else []),difficulty_basis='Uploaded editorial rating.',taxonomy_basis='Uploaded taxonomy; review before publishing.')
                if owner is not None and job.target_exam_id and kind=='PRACTICE':
                    question.exam=job.target_exam
                    question.source_year=int(row['year']) if str(row.get('year','')).isdigit() else None
                # Duplicates are skipped (not errors) so one repeated question doesn't block the batch.
                # Rows with images are compared by their image names (they get new files on import).
                from .workbooks import image_names
                question.figure_paths=image_names(row)
                fingerprint=question.refresh_fingerprint()
                scope=('paper',paper.pk) if paper else ('tenant',owner.pk if owner else None)
                if fingerprint and (scope,fingerprint) in seen:
                    note_duplicate(source_row,f'Repeats row {seen[(scope,fingerprint)]} of this file; skipped.','row');continue
                original=question.duplicate()
                if original:
                    note_duplicate(source_row,f'Already exists as question #{original.pk}; skipped.',original.pk);continue
                if fingerprint:seen[(scope,fingerprint)]=row.get('_row',index)
                if paper and fingerprint:
                    elsewhere=Question.objects.filter(fingerprint=fingerprint,paper__exam_id=paper.exam_id).exclude(paper=paper).exclude(status='retired').select_related('paper').first()
                    if elsewhere:add_warning(source_row,f'Same question also appears in {elsewhere.paper} (#{elsewhere.pk}); imported because it is a different paper.')
                question.figure_paths=row_images(row,images,preview,f'job-{job.pk}')
                question.full_clean();question.save();concept,_=ConceptTag.objects.get_or_create(topic=topic,name=topic.name);QuestionConceptTag.objects.create(question=question,concept_tag=concept);created+=1
        except (ValueError,TypeError,ValidationError,ArithmeticError) as exc:errors.append(f'Row {row.get("_row",index)}: {exc}')
    for title,group in tests.items():
        try:
            row=group['row']
            if TimedTest.objects.filter(tenant=job.tenant,name=title).exists():raise ValueError(f'Test {title} already exists; use a new name.')
            test=TimedTest(tenant=job.tenant,name=title,exam=group['exam'],series=row.get('series',''),paper_number=int(row.get('paper_number') or 1),duration_minutes=int(row['duration_minutes']),subject=row.get('subject',''),questions=group['questions'],active=False,source=row.get('source','') if row.get('source','').startswith(('https://','http://')) else '')
            test.full_clean();test.save();created+=1
        except (ValueError,ValidationError) as exc:errors.append(f'Test {title}: {exc}')
    if preview or errors:transaction.set_rollback(True)
    return created,errors
