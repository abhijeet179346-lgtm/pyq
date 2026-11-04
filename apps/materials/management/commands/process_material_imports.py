from django.core.management.base import BaseCommand

from apps.materials.models import MaterialDocument
from apps.materials.services import process_material_document


class Command(BaseCommand):
    help = "Extract selectable text from queued Markdown, TXT, and text-based PDFs into topic sections."

    def add_arguments(self, parser): parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        documents = MaterialDocument.objects.filter(processing_status=MaterialDocument.ProcessingStatus.QUEUED)[:options["limit"]]
        for document in documents:
            try:
                count = process_material_document(document)
            except Exception as exc:
                document.processing_status = MaterialDocument.ProcessingStatus.FAILED
                document.processing_error = str(exc)[:500]
                document.save(update_fields=["processing_status", "processing_error"])
                self.stderr.write(f"{document.pk}: {document.processing_error}")
            else:
                self.stdout.write(f"{document.pk}: extracted {count} sections")
