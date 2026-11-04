from django.core.management.base import BaseCommand

from apps.scans.models import ScanImport
from apps.scans.services import process_scan


class Command(BaseCommand):
    help = "Process queued scan imports using the configured vision model."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--all-pages", action="store_true", help="Continue each import until every page is processed.")
        parser.add_argument("--retry-failed", action="store_true", help="Resume failed imports from the last saved page.")
        parser.add_argument("--tenant", help="Process only this tenant slug.")

    def handle(self, *args, **options):
        statuses = [ScanImport.Status.QUEUED]
        if options["retry_failed"]:
            statuses.append(ScanImport.Status.FAILED)
        scans = ScanImport.objects.filter(status__in=statuses)
        if options["tenant"]:
            scans = scans.filter(tenant__slug=options["tenant"])
        scans = list(scans.order_by("created_at")[:options["limit"]])
        for scan in scans:
            while True:
                before = scan.next_page
                process_scan(scan)
                self.stdout.write(f"{scan.pk}: {scan.status} ({scan.next_page}/{scan.page_count} pages, {scan.extracted_count} questions)")
                if not options["all_pages"] or scan.status != ScanImport.Status.QUEUED or scan.next_page <= before:
                    break
