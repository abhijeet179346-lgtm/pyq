from django.conf import settings

from .import_pyq_sample import Command as SampleCommand


class Command(SampleCommand):
    help = 'Import a small sourced, adapted COMEDK, MHT-CET and CUET UG evaluation set.'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.set_defaults(path=str(settings.BASE_DIR / 'data' / 'new_exam_samples.json'))
