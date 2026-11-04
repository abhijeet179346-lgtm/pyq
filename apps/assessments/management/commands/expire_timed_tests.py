from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from apps.assessments.models import TimedAttempt
from apps.assessments.timed_services import finish
class Command(BaseCommand):
    help='Finalize timed attempts whose server deadline has passed, including closed-browser attempts.'
    def handle(self,*args,**options):
        count=0
        for pk in TimedAttempt.objects.filter(status='in_progress',deadline__lte=timezone.now()).values_list('pk',flat=True):
            with transaction.atomic():
                item=TimedAttempt.objects.select_for_update().get(pk=pk)
                finish(item,expired=True);count+=1
        self.stdout.write(f'{count} attempts expired.')
