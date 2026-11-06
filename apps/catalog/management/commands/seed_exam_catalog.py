from django.core.management.base import BaseCommand

from apps.catalog.models import Exam, Subject
from apps.features.models import TenantExam
from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = 'Create the initial exam and subject catalogue without changing existing admin choices.'

    def handle(self, *args, **options):
        defaults = {
            'jee-main': ('JEE Main', ('Physics', 'Chemistry', 'Mathematics')),
            'jee-advanced': ('JEE Advanced', ('Physics', 'Chemistry', 'Mathematics')),
            'neet': ('NEET', ('Physics', 'Chemistry', 'Biology')),
            'comedk': ('COMEDK', ('Physics', 'Chemistry', 'Mathematics')),
            'mht-cet': ('MHT-CET', ('Physics', 'Chemistry', 'Mathematics', 'Biology')),
            'cuet-ug': ('CUET UG', ('English', 'General Test', 'Physics', 'Chemistry', 'Mathematics',
                                    'Biology', 'Accountancy', 'Business Studies', 'Economics',
                                    'History', 'Political Science', 'Geography', 'Computer Science')),
        }
        for slug, (name, names) in defaults.items():
            exam, created = Exam.objects.get_or_create(slug=slug, defaults={'name': name, 'active': True})
            if created:
                subjects = []
                for subject_name in names:
                    subject, _ = Subject.objects.get_or_create(name=subject_name,
                        defaults={'slug': subject_name.lower().replace(' ', '-')})
                    subjects.append(subject)
                exam.subjects.add(*subjects)
            if slug in ('comedk', 'mht-cet', 'cuet-ug'):
                master = Tenant.objects.filter(is_master=True).first()
                if master:
                    TenantExam.objects.get_or_create(tenant=master, exam=exam, defaults={'enabled': True})
        self.stdout.write('Exam catalogue ready; existing subject selections preserved.')
