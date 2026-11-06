from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.catalog.models import Exam, Subject
from apps.features.models import TenantExam
from apps.tenancy.models import Tenant


class TenantExamChecklistTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name='Checklist Institute', slug='checklist-inst')
        physics = Subject.objects.get_or_create(name='Physics', defaults={'slug': 'physics'})[0]
        cls.jee = Exam.objects.get_or_create(slug='jee-main', defaults={'name': 'JEE Main', 'active': True})[0]
        cls.neet = Exam.objects.get_or_create(slug='neet', defaults={'name': 'NEET', 'active': True})[0]
        cls.jee.subjects.add(physics)
        cls.root = User.objects.create_superuser(username='checkroot', email='checkroot@example.test',
            password='Check-root-password-27', email_verified_at=timezone.now())

    def setUp(self):
        self.client = Client(HTTP_HOST='localhost')
        self.client.force_login(self.root)
        self.url = f'/admin/tenancy/tenant/{self.tenant.pk}/change/'

    def save(self, exam_ids):
        form = self.client.get(self.url).context['adminform'].form
        data = {name: value for name, value in form.initial.items()
                if name in form.fields and name != 'exams' and value is not None}
        data.update({'exams': [str(pk) for pk in exam_ids],
                     'feature_entitlements-TOTAL_FORMS': '0', 'feature_entitlements-INITIAL_FORMS': '0'})
        return self.client.post(self.url, data)

    def enabled(self):
        return set(TenantExam.objects.filter(tenant=self.tenant, enabled=True).values_list('exam__slug', flat=True))

    def test_checklist_lists_exams_with_subjects(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'JEE Main  (Physics)')
        self.assertContains(response, 'NEET')

    def test_ticking_and_unticking_exams(self):
        response = self.save([self.jee.pk, self.neet.pk])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.enabled(), {'jee-main', 'neet'})
        self.save([self.jee.pk])
        self.assertEqual(self.enabled(), {'jee-main'})
        self.assertFalse(TenantExam.objects.get(tenant=self.tenant, exam=self.neet).enabled)
        self.assertTrue(AuditEvent.objects.filter(tenant=self.tenant, action='tenant_exams_updated').exists())
        listing = self.client.get('/admin/tenancy/tenant/')
        self.assertContains(listing, 'JEE Main')

    def test_enable_all_action(self):
        self.client.post('/admin/tenancy/tenant/', {'action': 'enable_all_exams', '_selected_action': [self.tenant.pk]})
        self.assertTrue({'jee-main', 'neet'} <= self.enabled())
