from django.conf import settings
from .import_pyq_sample import Command as SampleCommand


class Command(SampleCommand):
    help = "Import the 1400 source-attributed expansion questions. Combine with import_pyq_sample for 500 per exam."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.set_defaults(path=str(settings.BASE_DIR / "data" / "exam_expansion.json"))
