import json,re,zipfile,hashlib
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
rows=json.loads(zipfile.ZipFile(BASE.parent.parent/'work/pyq_sources/jeebench.zip').read('data/dataset.json'))
original=json.loads((BASE/'data/pyq_sample.json').read_text())['questions']+json.loads((BASE/'data/exam_expansion.json').read_text())['questions']
lookup={re.sub(r'\s+','',r['stem']).casefold():r for r in original}
output=[];seen=set()
for row in rows:
    if re.search(r'figure|diagram|shown|includegraphics|\\begin\{(?:tabular|array)|\\end\{itemize\}',row['question'],re.I):continue
    parts=re.split(r'\n\s*\(([ABCD])\)\s*',row['question'])
    numerical=row['type'] in ['Integer','Numeric']
    if not numerical and (len(parts)!=9 or not re.fullmatch('[ABCD]+',row['gold'])):continue
    stem=row['question'].strip() if numerical else parts[0].strip()
    if stem in seen:continue
    seen.add(stem)
    source=lookup.get(re.sub(r'\s+','',stem).casefold(),{})
    subject={'phy':'Physics','chem':'Chemistry','math':'Mathematics'}[row['subject']]
    kind='numerical' if numerical else 'multi_correct' if row['type']=='MCQ(multiple)' else 'single_correct'
    output.append({'key':hashlib.sha256((row['description']+str(row['index'])+row['subject']).encode()).hexdigest()[:20],
        'stem':stem,'options':[] if numerical else [{'id':parts[i],'text':parts[i+1].strip()} for i in range(1,9,2)],
        'answer':row['gold'] if numerical or kind=='single_correct' else list(row['gold']),'question_type':kind,'subject':subject,
        'chapter':source.get('chapter',''),'topic':source.get('topic',''),'subtopic':source.get('subtopic',''),
        'section':subject+' · '+kind.replace('_',' '),'source':'https://github.com/dair-iitd/jeebench','source_record':f"{row['description']} · {row['subject']} Q{row['index']}",
        'solution':source.get('solution','Worked solution not yet provided. Answer key supplied by JEEBench.'),
        'marking':{'correct':4 if kind=='multi_correct' else 3,'incorrect':-2 if kind=='multi_correct' else -1 if kind=='single_correct' else 0,'unanswered':0,'partial':kind=='multi_correct'}})
(BASE/'data/timed_test_questions.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
print('Timed-test source questions:',len(output))
