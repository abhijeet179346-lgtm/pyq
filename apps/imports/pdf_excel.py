from django.utils import timezone
import re
import pymupdf as fitz
from .models import ContentJob


def split_questions(text,page,job):
    # Extraction is a review aid. Complex layouts, equations and separate answer keys require checking.
    starts=list(re.finditer(r'(?m)^\s*(?:Q(?:uestion)?\.?\s*)?(\d{1,4})\s*[.)：:]\s+',text))
    rows=[]
    for index,match in enumerate(starts):
        block=text[match.end():starts[index+1].start() if index+1<len(starts) else len(text)].strip()
        if len(block)<12:continue
        original=block
        printed=re.search(r'(?im)^\s*(?:correct\s+)?answer\s*[:：]\s*([A-D](?:\s*,\s*[A-D])*|[-+]?\d+(?:\.\d+)?)\s*$',block)
        printed_answer=printed[1] if printed else ''
        if printed:block=block[:printed.start()]+block[printed.end():]
        solution=''
        solution_match=re.search(r'(?im)^\s*(?:solution|explanation)\s*[:：]\s*',block)
        if solution_match:solution=block[solution_match.end():].strip();block=block[:solution_match.start()].strip()
        options=list(re.finditer(r'(?m)^\s*\(?([A-Da-d])\s*[).：:]\s*',block))
        stem=block[:options[0].start()].strip() if options else block
        row={key:'' for key in ['option_a','option_b','option_c','option_d','answer','solution','subject','chapter','topic','subtopic','difficulty','exam','year']}
        row.update(source_id=f'pdf-{job.pk}-p{page}-q{match[1]}',kind='PRACTICE',question_type='single_correct' if options else 'numerical',stem=stem,source=job.source,license=job.license,source_page=page,review_status='needs_review',warnings='Check numbering, equations, continuation and answer key. No answer inferred.',original_text=original)
        for pos,option in enumerate(options):row['option_'+option[1].lower()]=block[option.end():options[pos+1].start() if pos+1<len(options) else len(block)].strip()
        row['answer']=printed_answer;row['solution']=solution
        if ',' in printed_answer:row['question_type']='multi_correct'
        rows.append(row)
    return rows


def process_pdf_chunk(job,page_batch=15):
    if job.kind!='pdf':return
    claimed=ContentJob.objects.filter(pk=job.pk,status__in=['queued','failed']).update(status='processing',updated_at=timezone.now())
    if not claimed:
        job.refresh_from_db();return
    job.refresh_from_db()
    job.status='processing'
    try:
        job.file.open('rb')
        pdf=fitz.open(stream=job.file.read(),filetype='pdf')
        if pdf.is_encrypted:raise ValueError('Password-protected PDFs must be unlocked before upload.')
        if len(pdf)>1500:raise ValueError('Maximum 1500 pages per job; split larger PDFs.')
        job.page_count=len(pdf);stop=min(job.next_page+page_batch,len(pdf))
        for index in range(job.next_page,stop):
            page=pdf[index];text=page.get_text('text',sort=True).strip();warning=''
            if len(text)<30 and job.use_ocr:
                from apps.scans.services import _request_page
                image=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
                result=_request_page(image,[],tenant=job.tenant,user=job.initiated_by)
                for number,row in enumerate(result.get('questions',[]),1):
                    values={key:'' for key in ['subject','chapter','topic','subtopic','exam','year']}
                    values.update(source_id=f'ocr-{job.pk}-p{index+1}-{number}',kind='PRACTICE',stem=row.get('stem',''),answer=row.get('answer',''),solution=row.get('solution',''),question_type=row.get('question_type','single_correct'),difficulty=row.get('difficulty',''),source=job.source,license=job.license,source_page=index+1,review_status='needs_review',warnings='OCR output. Verify all equations, figures and answers against the source page.',original_text=text)
                    for pos,option in enumerate(row.get('options',[])[:4]):values['option_'+chr(97+pos)]=str(option.get('text','')) if isinstance(option,dict) else str(option)
                    job.rows.append(values)
                warning='OCR extraction; human review required.'
            elif len(text)<30:warning='No usable text; scanned page needs configured OCR.'
            else:job.rows.extend(split_questions(text,index+1,job))
            job.pages.append({'page':index+1,'text':text,'warnings':warning or 'Question segmentation is heuristic; check source page.'})
            job.next_page=index+1
            job.save(update_fields=['page_count','next_page','rows','pages','updated_at'])
        pdf.close()
        job.status='review' if job.next_page>=job.page_count else 'queued'
        job.errors=[]
        job.save(update_fields=['page_count','next_page','rows','pages','status','errors','updated_at'])
    except Exception as exc:
        job.status='failed';job.errors=[str(exc)[:1000]];job.save(update_fields=['status','errors','updated_at'])
    finally:job.file.close()
