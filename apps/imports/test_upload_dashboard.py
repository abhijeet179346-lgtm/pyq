import shutil
import tempfile

import pymupdf as fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Exam, Subject
from apps.content.models import Question
from apps.features.models import TenantExam
from apps.tenancy.models import Tenant

from .answer_keys import apply_answer_key, is_answer_key_page, parse_answer_key
from .models import ContentJob
from .test_admin_uploads import XLSX, entitle, q
from .workbooks import workbook_bytes

TMP_MEDIA = tempfile.mkdtemp()
URL = '/admin/imports/contentjob/upload/'


def make_pdf(pages):
    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_text((50, 60), text, fontsize=10)
    raw = doc.tobytes()
    doc.close()
    return raw


PAPER = [
    "1. A body of mass 2 kg moves at 3 m/s. Its kinetic energy is\n(A) 9 J\n(B) 6 J\n(C) 3 J\n(D) 18 J\n"
    "2. Which gas is evolved when zinc reacts with dilute HCl?\n(A) Oxygen\n(B) Hydrogen\n(C) Chlorine\n(D) Nitrogen\n"
    "3. The SI unit of electric charge is the\n(A) volt\n(B) ampere\n(C) coulomb\n(D) ohm",
]
KEY_PAGE = "ANSWER KEY\n1. (A)   2. (B)   3. (C)\n4. (D)   5. (A)   6. (B)"


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class UploadDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = Tenant.objects.get(is_master=True)
        cls.institute = Tenant.objects.create(name='Dash Institute', slug='dash-inst')
        entitle(cls.master, 'bulk_question_import', 'practice')
        entitle(cls.institute, 'bulk_question_import', 'practice')
        subjects = {n: Subject.objects.get_or_create(name=n, defaults={'slug': n.lower()})[0]
                    for n in ('Physics', 'Chemistry', 'Mathematics', 'Biology')}
        cls.subjects = subjects
        cls.jee = Exam.objects.get_or_create(slug='jee-main', defaults={'name': 'JEE Main', 'active': True})[0]
        cls.neet = Exam.objects.get_or_create(slug='neet', defaults={'name': 'NEET', 'active': True})[0]
        cls.jee.subjects.add(subjects['Physics'], subjects['Chemistry'], subjects['Mathematics'])
        cls.neet.subjects.add(subjects['Physics'], subjects['Chemistry'], subjects['Biology'])
        TenantExam.objects.update_or_create(tenant=cls.institute, exam=cls.jee, defaults={'enabled': True})
        TenantExam.objects.update_or_create(tenant=cls.institute, exam=cls.neet, defaults={'enabled': False})
        cls.root = User.objects.create_superuser(username='dashroot', email='dashroot@example.test',
            password='Dash-root-password-27', email_verified_at=timezone.now())
        cls.admin = User.objects.create_user(username='dashadmin', email='dash@inst.example.test',
            password='Dash-admin-password-27', tenant=cls.institute, role='tenant_admin', is_staff=True,
            email_verified_at=timezone.now())

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def client_for(self, user):
        client = Client(HTTP_HOST='localhost')
        client.force_login(user)
        return client

    def post(self, user, tenant, exam, subject, name, content, ctype=XLSX, **extra):
        data = {'tenant': tenant.pk, 'exam': exam.pk, 'subject': subject.pk, 'source': 'Dash test',
                'license': 'Test only', 'file': SimpleUploadedFile(name, content, content_type=ctype), **extra}
        return self.client_for(user).post(URL, data)

    def test_tenant_admin_sees_only_enabled_exams_and_their_subjects(self):
        response = self.client_for(self.admin).get(URL)
        self.assertEqual(response.status_code, 200)
        exams = list(response.context['form'].fields['exam'].queryset)
        self.assertEqual(exams, [self.jee])
        self.assertEqual([name for _, name in response.context['exam_subjects'][self.jee.pk]],
                         ['Chemistry', 'Mathematics', 'Physics'])

    def test_subject_must_belong_to_exam(self):
        response = self.post(self.admin, self.institute, self.jee, self.subjects['Biology'], 'q.xlsx', b'x')
        self.assertEqual(response.status_code, 200)
        self.assertIn('not a subject of JEE Main', str(response.context['form'].errors))
        self.assertFalse(ContentJob.objects.exists())

    def test_exam_not_enabled_for_institute_is_rejected(self):
        response = self.post(self.admin, self.institute, self.neet, self.subjects['Biology'], 'q.xlsx', b'x')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('exam'))

    def test_tenant_admin_cannot_upload_for_another_institute(self):
        other = Tenant.objects.create(name='Other', slug='other-inst')
        response = self.post(self.admin, other, self.jee, self.subjects['Physics'], 'q.xlsx', b'x')
        self.assertEqual(response.status_code, 200)
        self.assertIn('tenant', response.context['form'].errors)
        self.assertFalse(ContentJob.objects.exists())

    def test_institute_excel_upload_creates_private_tagged_questions(self):
        row = q(source_id='dash-1', kind='', exam='', subject='', year='', stem='Private institute question')
        response = self.post(self.admin, self.institute, self.jee, self.subjects['Physics'], 'q.xlsx',
                             workbook_bytes('questions', [row]), year='2024')
        job = ContentJob.objects.get()
        self.assertRedirects(response, f'/admin/imports/contentjob/{job.pk}/review/', fetch_redirect_response=False)
        self.assertEqual((job.kind, job.status), ('questions', 'review'), job.errors)
        self.client_for(self.admin).post(f'/admin/imports/contentjob/{job.pk}/review/', {'action': 'commit', 'reviewed': 'yes'})
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed', job.errors)
        made = Question.objects.get(stem='Private institute question')
        self.assertEqual((made.tenant_id, made.kind, made.paper_id), (self.institute.pk, 'PRACTICE', None))
        self.assertEqual(made.topic.chapter.subject.name, 'Physics')
        self.assertIn('JEE Main', made.tags)
        self.assertIn('2024', made.tags)

    def test_superuser_master_upload_needs_year_and_makes_global_pyqs(self):
        row = q(source_id='dash-pyq', kind='', exam='', subject='', year='', stem='Global PYQ from dashboard')
        response = self.post(self.root, self.master, self.jee, self.subjects['Physics'], 'q.xlsx', workbook_bytes('questions', [row]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('year', response.context['form'].errors)
        self.post(self.root, self.master, self.jee, self.subjects['Physics'], 'q.xlsx', workbook_bytes('questions', [row]), year='2023')
        job = ContentJob.objects.get()
        self.client_for(self.root).post(f'/admin/imports/contentjob/{job.pk}/review/', {'action': 'commit', 'reviewed': 'yes'})
        made = Question.objects.get(stem='Global PYQ from dashboard')
        self.assertEqual((made.kind, made.tenant_id, made.paper.exam_id, made.paper.year), ('PYQ', None, self.jee.pk, 2023))

    def test_pdf_with_answer_key_at_the_end(self):
        pdf = make_pdf(PAPER + [KEY_PAGE])
        self.post(self.admin, self.institute, self.jee, self.subjects['Physics'], 'paper.pdf', pdf,
                  ctype='application/pdf', year='2024')
        job = ContentJob.objects.get()
        self.assertEqual((job.kind, job.status), ('pdf', 'review'), job.errors)
        self.assertEqual(len(job.rows), 3, job.rows)  # the key page is not read as questions
        self.assertEqual([r['answer'] for r in job.rows], ['A', 'B', 'C'])
        self.assertTrue(all(r['exam'] == 'jee-main' and r['subject'] == 'Physics' and r['year'] == '2024'
                            and r['kind'] == 'PRACTICE' for r in job.rows))
        self.assertIn('3 filled', job.answer_key_summary)
        self.assertTrue(job.pages[-1]['answer_key'])

    def test_pdf_with_separate_answer_key(self):
        self.post(self.admin, self.institute, self.jee, self.subjects['Physics'], 'paper.pdf', make_pdf(PAPER),
                  ctype='application/pdf',
                  answer_key=SimpleUploadedFile('key.pdf', make_pdf(["Answers\n1 - C\n2 - B\n3 - C\n4 - A\n5 - B"]),
                                                content_type='application/pdf'))
        job = ContentJob.objects.get()
        self.assertEqual([r['answer'] for r in job.rows], ['C', 'B', 'C'])
        self.assertIn('Answer key', job.answer_key_summary)
        review = self.client_for(self.admin).get(f'/admin/imports/contentjob/{job.pk}/review/')
        self.assertContains(review, 'Upload the corrected workbook')
        self.assertContains(review, '3 filled')

    def test_answer_key_rejected_for_excel(self):
        response = self.post(self.admin, self.institute, self.jee, self.subjects['Physics'], 'q.xlsx', b'x',
                             answer_key=SimpleUploadedFile('key.pdf', b'%PDF', content_type='application/pdf'))
        self.assertIn('answer_key', response.context['form'].errors)


class AnswerKeyParsingTests(SimpleTestCase):
    def test_common_formats(self):
        self.assertEqual(parse_answer_key('1. (A) 2) b 3 - C 4: (2) Q5. D 6. 12.5 7. A,C'),
                         {1: 'A', 2: 'B', 3: 'C', 4: '2', 5: 'D', 6: '12.5', 7: 'A,C'})

    def test_key_page_detection(self):
        self.assertTrue(is_answer_key_page(KEY_PAGE))
        self.assertFalse(is_answer_key_page(PAPER[0]))
        self.assertFalse(is_answer_key_page('1. A 2. B'))  # too few pairs

    def test_numeric_labels_map_to_letters_and_conflicts_are_kept(self):
        rows = [{'source_id': 'pdf-1-p1-q1', 'option_a': 'x', 'option_b': 'y', 'answer': '', 'warnings': ''},
                {'source_id': 'pdf-1-p1-q2', 'option_a': 'x', 'option_b': 'y', 'answer': 'A', 'warnings': ''},
                {'source_id': 'pdf-1-p1-q3', 'answer': '', 'warnings': ''}]
        summary = apply_answer_key(rows, {1: '3', 2: 'B', 3: '42'})
        self.assertEqual([r['answer'] for r in rows], ['C', 'A', '42'])
        self.assertEqual(rows[2]['question_type'], 'numerical')
        self.assertIn('paper printed A', rows[1]['warnings'])
        self.assertIn('1 disagree', summary)

    def test_repeated_numbers_are_not_matched(self):
        rows = [{'source_id': 'pdf-1-p1-q1', 'answer': ''}, {'source_id': 'pdf-1-p2-q1', 'answer': ''}]
        self.assertIn('not applied', apply_answer_key(rows, {1: 'A'}))
        self.assertEqual([r['answer'] for r in rows], ['', ''])
