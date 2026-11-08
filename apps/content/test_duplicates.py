import importlib
import shutil
import tempfile

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Chapter, Exam, Paper, Subject, Topic
from apps.content.fingerprint import question_fingerprint
from apps.content.models import Question, without_duplicates
from apps.features.models import TenantExam
from apps.imports.models import ContentJob
from apps.imports.test_admin_uploads import XLSX, entitle, q
from apps.imports.workbooks import workbook_bytes
from apps.tenancy.models import Tenant

TMP_MEDIA = tempfile.mkdtemp()
STEM = 'A body of mass 2 kg moves at 3 m/s. What is its kinetic energy?'
OPTIONS = [{'id': 'A', 'text': '9 J'}, {'id': 'B', 'text': '6 J'}, {'id': 'C', 'text': '3 J'}, {'id': 'D', 'text': '18 J'}]


class FingerprintTests(SimpleTestCase):
    def test_cosmetic_differences_match(self):
        a = question_fingerprint(STEM, OPTIONS)
        b = question_fingerprint('  a BODY of mass 2 kg moves at 3 m/s -- what is its kinetic energy  ', list(reversed(OPTIONS)))
        self.assertEqual(a, b)
        self.assertEqual(question_fingerprint('Find $x$ if $x^2 = 4$ and x is positive', []),
                         question_fingerprint('Find x if \\left x^2 = 4 \\right and x is positive', []))

    def test_different_options_or_short_stems_do_not_match(self):
        self.assertNotEqual(question_fingerprint(STEM, OPTIONS), question_fingerprint(STEM, OPTIONS[:3] + [{'id': 'D', 'text': '12 J'}]))
        self.assertEqual(question_fingerprint('Q1', []), '')


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class DuplicatePreventionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = Tenant.objects.get(is_master=True)
        cls.institute = Tenant.objects.create(name='Dup Institute', slug='dup-inst')
        entitle(cls.master, 'bulk_question_import', 'practice')
        entitle(cls.institute, 'bulk_question_import', 'practice')
        cls.physics = Subject.objects.get_or_create(name='Physics', defaults={'slug': 'physics'})[0]
        cls.exam = Exam.objects.get_or_create(slug='jee-main', defaults={'name': 'JEE Main', 'active': True})[0]
        cls.exam.subjects.add(cls.physics)
        TenantExam.objects.update_or_create(tenant=cls.institute, exam=cls.exam, defaults={'enabled': True})
        chapter = Chapter.objects.create(subject=cls.physics, name='Dup chapter')
        cls.topic = Topic.objects.create(chapter=chapter, name='Dup topic')
        cls.paper_2023 = Paper.objects.create(exam=cls.exam, year=2023)
        cls.paper_2024 = Paper.objects.create(exam=cls.exam, year=2024)
        cls.root = User.objects.create_superuser(username='duproot', email='duproot@example.test',
            password='Dup-root-password-27', email_verified_at=timezone.now())
        cls.admin = User.objects.create_user(username='dupadmin', email='dup@inst.example.test',
            password='Dup-admin-password-27', tenant=cls.institute, role='tenant_admin', is_staff=True,
            email_verified_at=timezone.now())

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def make(self, **extra):
        values = dict(kind='PRACTICE', tenant=self.institute, stem=STEM, options=OPTIONS, answer='A',
                      topic=self.topic, source='Dup test', license='Test only')
        values.update(extra)
        return Question(**values)

    def test_database_blocks_same_bank_and_same_paper(self):
        self.make().save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make(stem=STEM.upper()).save()
        pyq = dict(kind='PYQ', tenant=None, paper=self.paper_2023)
        self.make(**pyq).save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make(**pyq).save()
        self.make(kind='PYQ', tenant=None, paper=self.paper_2024).save()  # another paper: allowed

    def test_retired_copies_do_not_count_and_clean_explains(self):
        original = self.make()
        original.save()
        copy = self.make()
        with self.assertRaisesMessage(ValidationError, f'already exists as #{original.pk}'):
            copy.full_clean()
        original.status = 'retired'
        original.save()
        copy.full_clean()
        copy.save()

    def test_without_duplicates_for_bulk_paths(self):
        self.make().save()
        batch = [self.make(), self.make(stem='A completely different question about momentum and force'),
                 self.make(stem='A completely different question about momentum and force')]
        kept = without_duplicates(batch)
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0].fingerprint)

    def upload(self, user, tenant, rows, exam=True):
        client = Client(HTTP_HOST='localhost')
        client.force_login(user)
        data = {'tenant': tenant.pk, 'exam': self.exam.pk, 'subject': self.physics.pk, 'year': '2024',
                'source': 'Dup test', 'license': 'Test only',
                'file': SimpleUploadedFile('dup.xlsx', workbook_bytes('questions', rows), content_type=XLSX)}
        client.post('/admin/imports/contentjob/upload/', data)
        job = ContentJob.objects.order_by('-pk').first()
        review = client.get(f'/admin/imports/contentjob/{job.pk}/review/')
        client.post(f'/admin/imports/contentjob/{job.pk}/review/', {'action': 'commit', 'reviewed': 'yes'})
        job.refresh_from_db()
        return job, review

    def row(self, source_id, stem=STEM):
        return q(source_id=source_id, kind='', exam='', subject='', year='', chapter='Dup chapter', topic='Dup topic',
                 stem=stem, option_a='9 J', option_b='6 J', option_c='3 J', option_d='18 J')

    def test_import_skips_existing_and_repeated_rows(self):
        existing = self.make()
        existing.save()
        rows = [self.row('d1'), self.row('d2', 'Which quantity is conserved in an elastic collision?'),
                self.row('d3', 'Which quantity is conserved in an elastic collision?')]
        job, review = self.upload(self.admin, self.institute, rows)
        self.assertEqual(job.status, 'completed', job.errors)
        self.assertEqual(job.created_count, 1)
        self.assertEqual(Question.objects.filter(tenant=self.institute).count(), 2)
        self.assertEqual([r.get('duplicate_of') for r in job.rows], [str(existing.pk), None, 'row'])
        self.assertContains(review, '2 rows already exist')
        made = Question.objects.get(tenant=self.institute, stem__startswith='Which quantity')
        self.assertEqual((made.exam_id, made.source_year), (self.exam.pk, 2024))

    def test_same_pyq_in_another_paper_imports_with_warning(self):
        self.make(kind='PYQ', tenant=None, paper=self.paper_2023).save()
        job, _ = self.upload(self.root, self.master, [self.row('p1')])
        self.assertEqual((job.status, job.created_count), ('completed', 1), job.errors)
        self.assertIn('also appears in', job.rows[0]['warnings'])


class RetireMigrationTests(TestCase):
    def test_migration_keeps_published_copy_and_retires_extras(self):
        tenant = Tenant.objects.create(name='Mig Institute', slug='mig-inst')
        subject = Subject.objects.get_or_create(name='Physics', defaults={'slug': 'physics'})[0]
        topic = Topic.objects.create(chapter=Chapter.objects.create(subject=subject, name='Mig'), name='Mig')
        base = dict(kind='PRACTICE', tenant=tenant, stem=STEM, options=OPTIONS, answer='A', topic=topic,
                    source='s', license='l')
        draft = Question.objects.create(**{**base, 'stem': 'temporary stem one for migration test'})
        published = Question.objects.create(**{**base, 'stem': 'temporary stem two for migration test', 'status': 'published'})
        # Simulate pre-migration data: identical stems, no fingerprints yet.
        Question.objects.filter(pk__in=[draft.pk, published.pk]).update(stem=STEM, fingerprint='')
        migration = importlib.import_module('apps.content.migrations.0006_question_fingerprint')
        migration.fingerprint_and_retire_duplicates(django_apps, None)
        draft.refresh_from_db()
        published.refresh_from_db()
        self.assertEqual((draft.status, published.status), ('retired', 'published'))
        self.assertEqual(draft.fingerprint, published.fingerprint)
