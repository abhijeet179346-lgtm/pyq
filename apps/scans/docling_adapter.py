"""Optional local layout/OCR pass before question extraction.

Docling is deliberately separate from the web process dependency set. Install
requirements-ocr.txt in a worker environment before selecting this backend.
"""

from io import BytesIO


def extract_page_markdown(pdf_pages):
    """Return one Markdown string per page in a small PDF chunk."""
    try:
        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise RuntimeError("Docling OCR is selected but not installed. Install requirements-ocr.txt on the worker.") from exc

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.do_formula_enrichment = True
    options.enable_remote_services = False
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )
    result = converter.convert(DocumentStream(name="scan-chunk.pdf", stream=BytesIO(pdf_pages)))
    return [result.document.export_to_markdown(page_no=index) for index in range(1, len(result.document.pages) + 1)]
