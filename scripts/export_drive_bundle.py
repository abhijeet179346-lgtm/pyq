"""Export local PYQs into a Drive-ready Exam/Subject folder tree.

Run from the project root: python manage.py shell < scripts/export_drive_bundle.py
Exports current local database content as Excel files and a ZIP bundle.
"""
import json
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path

from apps.content.models import Question
from apps.imports.workbooks import workbook_bytes


root = Path.cwd()
output = root / 'data/google_drive_upload'
archive = root / 'data/google_drive_upload.zip'
if output.exists():
    shutil.rmtree(output)
output.mkdir(parents=True)
if archive.exists():
    archive.unlink()

exam_names = {
    'comedk': 'COMEDK', 'cuet-ug': 'CUET UG', 'jee-advanced': 'JEE Advanced',
    'jee-main': 'JEE Main', 'mht-cet': 'MHT-CET', 'neet': 'NEET',
}
grouped = defaultdict(list)
questions = Question.objects.filter(kind='PYQ', status__in=('published', 'draft')).select_related(
    'paper__exam', 'topic__chapter__subject', 'subtopic'
).order_by('paper__exam__slug', 'topic__chapter__subject__name', 'status', 'id')

for question in questions:
    exam_slug = question.paper.exam.slug
    exam_label = exam_names.get(exam_slug, question.paper.exam.name)
    subject = question.topic.chapter.subject.name
    options = question.options if isinstance(question.options, list) else []
    option_map = {}
    for index, option in enumerate(options):
        if isinstance(option, dict):
            key = str(option.get('id') or chr(ord('a') + index)).lower()
            option_map[key] = option.get('text', option.get('content', ''))
        else:
            option_map[chr(ord('a') + index)] = option
    row = {
        'source_id': f'pyq-vault-{question.pk}', 'kind': 'PYQ', 'exam': exam_slug,
        'year': question.paper.year, 'paper': question.paper.sitting, 'subject': subject,
        'chapter': question.topic.chapter.name, 'topic': question.topic.name,
        'subtopic': question.subtopic.name if question.subtopic_id else '',
        'difficulty': question.difficulty, 'question_type': question.question_type,
        'stem': question.stem, 'option_a': option_map.get('a', ''),
        'option_b': option_map.get('b', ''), 'option_c': option_map.get('c', ''),
        'option_d': option_map.get('d', ''), 'answer': question.answer,
        'solution': question.solution, 'source': question.source,
        'license': question.license, 'source_page': question.source_page or '',
        'review_status': 'Published' if question.status == 'published' else 'Draft — subject review required',
        'original_text': question.stem,
    }
    grouped[(exam_label, subject, question.status)].append(row)

for (exam, subject, status), rows in sorted(grouped.items()):
    folder = output / exam / subject
    folder.mkdir(parents=True, exist_ok=True)
    label = 'Published PYQs' if status == 'published' else 'Drafts - subject review required'
    filename = f'{label}.xlsx'
    (folder / filename).write_bytes(workbook_bytes('questions', rows))

readme = '''# PYQ Vault Google Drive upload bundle

The folder structure is `Exam/Subject/`. Each Excel workbook contains questions from the local PYQ Vault database, with source URLs, source page where available, answer, and LaTeX text preserved. Published items and drafts are in separate workbooks. Drafts need subject review before publication. Workbooks are formatted using the app's own question importer columns.

Original question/paper source links are recorded in each row. The data includes web-sourced 2024 COMEDK, CUET UG, and MHT-CET material as well as the existing JEE Main, JEE Advanced, and NEET banks. Source answer keys have not all been independently verified; please review draft workbooks before use.

To update this bundle after changing the local database, run `python manage.py shell < scripts/export_drive_bundle.py` from the project root. The ZIP can be uploaded to Drive as a reference package; Google Drive does not automatically recreate folders from a ZIP, so unzip it locally first if individual folders/files need to appear separately.
'''
(output / 'README.md').write_text(readme)

with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
    for path in sorted(output.rglob('*')):
        if path.is_file():
            zipped.write(path, path.relative_to(output))

print(f'Exported {sum(map(len, grouped.values()))} questions into {len(grouped)} subject/status workbooks.')
print(f'Drive-ready folder: {output}')
print(f'ZIP: {archive} ({archive.stat().st_size} bytes)')
