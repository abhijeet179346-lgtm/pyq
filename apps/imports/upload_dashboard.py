"""Simple question-upload dashboard: institute -> exam -> subject -> PDF/Excel (+ optional answer key)."""
from django import forms
from django.core.exceptions import PermissionDenied

from apps.catalog.models import Exam, Subject
from apps.features.models import TenantExam
from apps.tenancy.models import Tenant

QUESTION_FILES = ('.pdf', '.xlsx', '.zip')


def exams_for(user, tenant=None):
    exams = Exam.objects.filter(active=True).prefetch_related('subjects').order_by('name')
    if user.is_superuser and (tenant is None or tenant.is_master):
        return exams
    tenant_id = tenant.pk if tenant else getattr(user, 'tenant_id', None)
    enabled = TenantExam.objects.filter(tenant_id=tenant_id, enabled=True).values_list('exam_id', flat=True)
    return exams.filter(pk__in=enabled)


class QuestionUploadForm(forms.Form):
    tenant = forms.ModelChoiceField(queryset=Tenant.objects.order_by('-is_master', 'name'), label='Institute')
    exam = forms.ModelChoiceField(queryset=Exam.objects.none(), empty_label='Choose an exam')
    subject = forms.ModelChoiceField(queryset=Subject.objects.order_by('name'), empty_label='Choose a subject')
    year = forms.IntegerField(required=False, min_value=1900, max_value=2100,
                              help_text='Exam year. Required for global PYQs; used as a tag for institute questions.')
    file = forms.FileField(label='Questions file', help_text='PDF question paper, Excel workbook (.xlsx), or a .zip with the workbook and its images.')
    answer_key = forms.FileField(required=False, label='Separate answer key (PDF)',
                                 help_text='Only for a PDF upload whose answers are in a separate file. If the key is printed at the end of the paper, leave this empty.')
    source = forms.CharField(max_length=240, help_text='Where the questions come from, e.g. "JEE Main 2024 Shift 1 official paper".')
    license = forms.CharField(max_length=160, label='Licence / rights', help_text='Your right to use this material, e.g. "Institute-owned".')
    use_ocr = forms.BooleanField(required=False, label='Scanned PDF: use vision OCR',
                                 help_text='Needs an AI budget and the scan import feature. Without it, scanned pages use local OCR.')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if not user.is_superuser:
            self.fields['tenant'].queryset = Tenant.objects.filter(pk=getattr(user, 'tenant_id', None))
            self.fields['tenant'].initial = getattr(user, 'tenant_id', None)
            self.fields['tenant'].widget = forms.HiddenInput()
        self.fields['exam'].queryset = exams_for(user)

    def exam_subjects(self):
        """{exam id: [[subject id, name], ...]} for the subject picker."""
        return {exam.pk: [[s.pk, s.name] for s in sorted(exam.subjects.all(), key=lambda s: s.name)]
                for exam in self.fields['exam'].queryset}

    def clean(self):
        data = super().clean()
        tenant, exam, subject = data.get('tenant'), data.get('exam'), data.get('subject')
        upload, key = data.get('file'), data.get('answer_key')
        if not self.user.is_superuser and tenant and tenant.pk != getattr(self.user, 'tenant_id', None):
            raise PermissionDenied('You can only upload for your own institute.')
        if tenant and exam and not exams_for(self.user, tenant).filter(pk=exam.pk).exists():
            self.add_error('exam', f'{exam} is not enabled for {tenant}. Enable it under the institute\'s exam settings first.')
        if exam and subject and not exam.subjects.filter(pk=subject.pk).exists():
            self.add_error('subject', f'{subject} is not a subject of {exam}.')
        if tenant and self.user.is_superuser and tenant.is_master and not data.get('year'):
            self.add_error('year', 'Global PYQs need the exam year.')
        if upload:
            name = upload.name.lower()
            if not name.endswith(QUESTION_FILES):
                self.add_error('file', 'Upload a PDF, an Excel workbook (.xlsx) or a .zip.')
            elif upload.size > (100 if name.endswith(('.pdf', '.zip')) else 25) * 1024 * 1024:
                self.add_error('file', 'Maximum size is 100 MB for PDF/ZIP and 25 MB for Excel.')
            if key and not name.endswith('.pdf'):
                self.add_error('answer_key', 'A separate answer key is only used with a PDF question paper.')
        if key:
            if not key.name.lower().endswith('.pdf'):
                self.add_error('answer_key', 'The answer key must be a PDF.')
            elif key.size > 25 * 1024 * 1024:
                self.add_error('answer_key', 'Maximum answer-key size is 25 MB.')
        return data

    def kind(self):
        return 'pdf' if self.cleaned_data['file'].name.lower().endswith('.pdf') else 'questions'
