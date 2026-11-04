import re

import pymupdf as fitz

from django.db import transaction
from django.utils import timezone

from .models import MaterialDocument, MaterialSection


@transaction.atomic
def process_material_document(document):
    if not document.source_file or not document.default_topic_id:
        raise ValueError("Choose a source file and a default topic before extracting material.")
    if document.sections.filter(question_links__isnull=False).exists():
        raise ValueError("This document has curated question links. Ingest a new document to preserve existing section references.")
    name = document.source_file.name.lower()
    document.source_file.open('rb')
    content = document.source_file.read()
    document.source_file.close()
    extracted = []
    if name.endswith(".pdf"):
        pdf = fitz.open(stream=content, filetype="pdf")
        if len(pdf) > 1500:
            raise ValueError("Study material extraction is limited to 1500 pages per document.")
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text("text").strip()
            if text:
                paragraphs=re.split(r"\n\s*\n",text)
                chunks=[];current=""
                for paragraph in paragraphs:
                    if len(current)+len(paragraph)>3000 and current:chunks.append(current);current=""
                    current+=("\n\n" if current else "")+paragraph
                if current:chunks.append(current)
                for part,chunk in enumerate(chunks,1):
                    first_line=next((line.strip() for line in chunk.splitlines() if len(line.strip())>5),f"Page {page_number}")
                    extracted.append((f"{first_line[:130]} · p{page_number}.{part}",chunk,str(page_number)))
        pdf.close()
    elif name.endswith((".md", ".markdown", ".txt")):
        text = content.decode("utf-8-sig")
        parts = re.split(r"(?m)^#{1,3}\s+", text)
        headings = re.findall(r"(?m)^#{1,3}\s+(.+)$", text)
        if len(parts) > 1:
            for index, body in enumerate(parts[1:]):
                title, _, rest = body.partition("\n")
                extracted.append((headings[index] if index < len(headings) else title.strip(), rest.strip(), ""))
        elif text.strip():
            extracted.append((document.title, text.strip(), ""))
    else:
        raise ValueError("Supported study materials are text PDF, Markdown, or TXT files.")
    if not extracted:
        raise ValueError("No selectable text was found. Scanned PDFs must use the OCR scan-import workflow.")
    # Keep stable section IDs, so existing question-to-section links do not break.
    present=[]
    for index,(title,body,page) in enumerate(extracted):
        section=document.sections.filter(order=index).order_by("pk").first()
        if section is None:section=MaterialSection(document=document,order=index,topic_id=document.default_topic_id)
        section.title=title[:200] or document.title;section.body=body;section.page_reference=page
        section.save();present.append(section.pk)
    document.sections.exclude(pk__in=present).update(active=False)
    document.processing_status = MaterialDocument.ProcessingStatus.COMPLETED
    document.processing_error = ""
    document.save(update_fields=["processing_status", "processing_error"])
    return len(extracted)
