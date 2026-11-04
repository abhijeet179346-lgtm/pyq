"""Backend XLSX interchange; exported strings are never executable formulas."""
import io,json,zipfile
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,PatternFill,Alignment
from django.core.exceptions import ValidationError

QUESTION_COLUMNS=['source_id','kind','exam','year','paper','subject','chapter','topic','subtopic','difficulty','question_type','stem','images','option_a','option_b','option_c','option_d','answer','solution','source','license','tags','source_page','review_status','warnings','original_text']
EXAM_COLUMNS=['exam','name','active']
TEST_COLUMNS=['test_name','series','paper_number','exam','duration_minutes','subject','chapter','topic','subtopic','section','source_id','question_type','stem','images','option_a','option_b','option_c','option_d','answer','solution','correct_marks','incorrect_marks','unanswered_marks','partial_credit','source','license']
MATERIAL_COLUMNS=['document','exam','subject','chapter','topic','title','body','page_reference','source','license']
COLUMNS={'questions':QUESTION_COLUMNS,'pdf':QUESTION_COLUMNS,'exams':EXAM_COLUMNS,'tests':TEST_COLUMNS,'materials':MATERIAL_COLUMNS}


IMAGE_EXTENSIONS=('.png','.jpg','.jpeg','.webp','.gif')


def open_upload(raw,filename):
    """Return (workbook bytes, {image basename: bytes}). A .zip holds one workbook plus image files."""
    if not filename.lower().endswith('.zip'):return raw,{}
    if len(raw)>100*1024*1024:raise ValueError('Zip uploads must be at most 100 MB.')
    try:archive=zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:raise ValueError('The upload is not a valid .zip file.') from exc
    entries=[item for item in archive.infolist() if not item.is_dir() and '__MACOSX' not in item.filename and not item.filename.rsplit('/',1)[-1].startswith('.')]
    if len(entries)>3000:raise ValueError('Zip uploads may contain at most 3,000 files.')
    if sum(item.file_size for item in entries)>200*1024*1024:raise ValueError('Uncompressed zip contents exceed 200 MB.')
    books=[item for item in entries if item.filename.lower().endswith('.xlsx')]
    if len(books)!=1:raise ValueError('The zip must contain exactly one .xlsx workbook.')
    images={}
    for item in entries:
        name=item.filename.rsplit('/',1)[-1].lower()
        if not name.endswith(IMAGE_EXTENSIONS):continue
        if item.file_size>5*1024*1024:raise ValueError(f'Image {name} is larger than 5 MB.')
        if name in images:raise ValueError(f'Two images are named {name}; image file names must be unique.')
        images[name]=archive.read(item)
    return archive.read(books[0]),images


def image_names(row):
    return [part.strip().rsplit('/',1)[-1].lower() for part in str(row.get('images','')).replace(';',',').split(',') if part.strip()]


def read_workbook(raw):
    if len(raw)>25*1024*1024:raise ValueError('Excel files must be at most 25 MB.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(item.file_size for item in archive.infolist())>100*1024*1024:raise ValueError('Uncompressed workbook exceeds 100 MB.')
    book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
    sheet=book['Questions'] if 'Questions' in book.sheetnames else book.worksheets[0]
    iterator=sheet.iter_rows();first=next(iterator,None)
    if not first:raise ValueError('Workbook has no headers.')
    headers=[str(cell.value or '').strip().casefold() for cell in first]
    if len(headers)>60 or len({h for h in headers if h})!=len([h for h in headers if h]):raise ValueError('Duplicate or excessive headers.')
    rows=[]
    for number,cells in enumerate(iterator,2):
        if number>10001:raise ValueError('Maximum 10,000 rows per workbook.')
        if any(cell.data_type=='f' for cell in cells):raise ValueError(f'Row {number}: formulas are not accepted. Paste values instead.')
        row={key:str(cell.value if cell.value is not None else '').strip() for key,cell in zip(headers,cells) if key}
        if any(row.values()):row['_row']=number;rows.append(row)
    book.close()
    if not rows:raise ValueError('Workbook contains no data rows.')
    return rows


def workbook_bytes(kind,rows,pages=None):
    book=Workbook();sheet=book.active;sheet.title='Questions' if kind=='pdf' else kind.capitalize()
    columns=COLUMNS[kind];sheet.append(columns)
    for row in rows:
        sheet.append([str(row.get(key,'') if row.get(key) is not None else '')[:32767] for key in columns])
    if pages:
        raw=book.create_sheet('Source pages');raw.append(['page','part','text','warnings'])
        for page in pages:
            content=str(page.get('text',''))
            for index,start in enumerate(range(0,max(len(content),1),32000),1):raw.append([page.get('page'),index,content[start:start+32000],page.get('warnings','')])
    info=book.create_sheet('Instructions');info.append(['Field','Meaning'])
    info.append(['Review','Review extracted text, options, answers and source rights before import. Missing answers are never guessed.'])
    info.append(['Import','Use the matching Excel import type. Questions need kind PRACTICE or PYQ, full taxonomy, difficulty, answer, source and licence.'])
    info.append(['Options','Use option_a through option_d. Single answer A–D; multiple answers A,C; numerical answers as text.'])
    info.append(['PDF','Pages and original_text preserve provenance. Layout-heavy or scanned content requires OCR and human review.'])
    for ws in book:
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for cell in ws[1]:cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='46688C')
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value,str):cell.data_type='s'
                cell.alignment=Alignment(vertical='top',wrap_text=True)
        for column in ws.columns:
            name=column[0].column_letter;header=str(column[0].value or '')
            ws.column_dimensions[name].width=65 if header in ['stem','body','solution','text','original_text','Meaning'] else 24
    stream=io.BytesIO();book.save(stream);return stream.getvalue()
