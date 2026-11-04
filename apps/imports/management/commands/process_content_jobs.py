from django.core.management.base import BaseCommand
from apps.imports.models import ContentJob
from apps.imports.pdf_excel import process_pdf_chunk
class Command(BaseCommand):
    help='Process queued PDF-to-Excel jobs incrementally. Re-run or schedule for large files.'
    def add_arguments(self,parser):
        parser.add_argument('--job',type=int)
        parser.add_argument('--all-pages',action='store_true')
    def handle(self,*args,**options):
        jobs=ContentJob.objects.filter(kind='pdf',status='queued')
        if options['job']:jobs=jobs.filter(pk=options['job'])
        for job in jobs:
            process_pdf_chunk(job)
            while options['all_pages'] and job.status=='queued':process_pdf_chunk(job)
            self.stdout.write(f'Job {job.pk}: {job.status}, {job.next_page}/{job.page_count} pages, {len(job.rows)} question rows')
