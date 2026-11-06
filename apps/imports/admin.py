from django.contrib import admin
from apps.tenancy.features import is_enabled
from .models import ImportBatch


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("file_name", "tenant", "initiated_by", "status", "row_count", "valid_count", "created_count", "created_at", "completed_at")
    list_filter = ("status", "created_at")
    search_fields = ("file_name", "initiated_by__email")
    readonly_fields = tuple(field.name for field in ImportBatch._meta.fields)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser: return qs
        if getattr(request.user, "role", None) == "teacher":
            return qs.filter(tenant_id=getattr(request.user, "tenant_id", None), initiated_by=request.user)
        return qs.filter(tenant_id=getattr(request.user, "tenant_id", None)) if getattr(request.user, "tenant_id", None) else qs.none()

    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "tenant_id", None) is not None and is_enabled(request.user.tenant, "import_reports"))
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or (getattr(request.user, "tenant_id", None) is not None and
            (obj is None or (obj.tenant_id == getattr(request.user, "tenant_id", None) and
             (getattr(request.user, "role", None) != "teacher" or obj.initiated_by_id == request.user.pk))))
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


from django import forms
from django.core.exceptions import PermissionDenied,ValidationError
from django.http import HttpResponse
from django.urls import path,reverse
from django.utils.html import format_html
from django.db import transaction
from apps.tenancy.models import Tenant
from .models import ContentJob
from .workbooks import workbook_bytes,read_workbook,COLUMNS
from .excel_services import apply_rows
from .pdf_excel import process_pdf_chunk


class ContentJobForm(forms.ModelForm):
    class Meta:
        model=ContentJob
        fields='__all__'
    def clean(self):
        data=super().clean();file=data.get('file');kind=data.get('kind')
        exam=data.get('target_exam');subject=data.get('target_subject')
        if kind=='questions' and bool(exam)!=bool(subject):
            raise forms.ValidationError('Choose both a destination exam and subject, or leave both blank for a mixed workbook.')
        if kind=='questions' and exam and subject and not exam.subjects.filter(pk=subject.pk).exists():
            raise forms.ValidationError('This subject is not enabled for the selected exam. Update the exam in admin first.')
        if file:
            name=file.name.lower()
            if kind=='pdf':allowed,limit=('.pdf',),100
            elif kind in ('questions','tests'):allowed,limit=('.xlsx','.zip'),(100 if name.endswith('.zip') else 25)
            else:allowed,limit=('.xlsx',),25
            if not name.endswith(allowed):raise forms.ValidationError('Upload a '+' or '.join(allowed)+' file for this job type.'+(' Use a .zip containing the workbook and image files when questions need images.' if '.zip' in allowed else ''))
            if file.size>limit*1024*1024:raise forms.ValidationError(f'Maximum file size is {limit} MB.')
        return data


def job_allowed(user,tenant,kind,use_ocr=False):
    if user.is_superuser:return True
    if getattr(user,'tenant_id',None)!=tenant.pk or getattr(user,'role',None)!='tenant_admin':return False
    # PDF-to-Excel is a question-import aid (text + local OCR); paid vision OCR also needs scan_import.
    feature={'questions':'bulk_question_import','tests':'tests','materials':'material_import','pdf':'bulk_question_import'}.get(kind)
    if kind=='pdf' and use_ocr and not is_enabled(tenant,'scan_import'):return False
    return bool(feature and is_enabled(tenant,feature))


@admin.register(ContentJob)
class ContentJobAdmin(admin.ModelAdmin):
    change_list_template='admin/imports/contentjob/change_list.html'
    form=ContentJobForm
    list_display=('id','kind','tenant','status','next_page','page_count','created_at','review_link')
    list_filter=('kind','status','tenant')
    readonly_fields=('initiated_by','status','next_page','page_count','errors','created_at','updated_at','preview_rows','download_link','template_links','created_count','review_link')
    exclude=('rows','pages')
    actions=['process_upload','commit_upload','download_excel']

    def has_module_permission(self,request):return request.user.is_superuser or getattr(request.user,'role',None)=='tenant_admin'
    def has_add_permission(self,request):return self.has_module_permission(request)
    def has_delete_permission(self,request,obj=None):return False
    def has_view_permission(self,request,obj=None):return self.has_module_permission(request) and (obj is None or job_allowed(request.user,obj.tenant,obj.kind,obj.use_ocr))
    def has_change_permission(self,request,obj=None):return self.has_view_permission(request,obj)
    def get_queryset(self,request):
        queryset=super().get_queryset(request)
        return queryset if request.user.is_superuser else queryset.filter(tenant_id=getattr(request.user,'tenant_id',None))
    def get_readonly_fields(self,request,obj=None):
        fields=list(self.readonly_fields)
        if obj:fields+=['file','kind','tenant','source','license','use_ocr','target_exam','target_subject']
        return fields
    def formfield_for_foreignkey(self,db_field,request,**kwargs):
        if db_field.name=='tenant' and not request.user.is_superuser:kwargs['queryset']=Tenant.objects.filter(pk=getattr(request.user,'tenant_id',None))
        return super().formfield_for_foreignkey(db_field,request,**kwargs)
    def save_model(self,request,obj,form,change):
        if not job_allowed(request.user,obj.tenant,obj.kind,obj.use_ocr):raise PermissionDenied('This upload is not enabled for this tenant. Vision OCR also requires the scan_import feature.')
        if not change:obj.initiated_by=request.user
        super().save_model(request,obj,form,change)
    def get_urls(self):
        return [path('<int:job_id>/review/',self.admin_site.admin_view(self.review_view),name='contentjob_review'),path('<int:job_id>/download/',self.admin_site.admin_view(self.download_view),name='contentjob_download'),path('template/<str:kind>/',self.admin_site.admin_view(self.template_view),name='contentjob_template')]+super().get_urls()
    def review_link(self,obj):
        if not obj or not obj.pk:return 'Save this upload to open its processing and review page.'
        return format_html('<a href="{}">Process &amp; review →</a>',reverse('admin:contentjob_review',args=[obj.pk]))
    def review_view(self,request,job_id):
        from django.shortcuts import get_object_or_404,render,redirect
        from django.core.paginator import Paginator
        from .job_services import process_job,commit_job
        obj=get_object_or_404(self.get_queryset(request),pk=job_id)
        if not self.has_view_permission(request,obj):raise PermissionDenied()
        if request.method=='POST':
            action=request.POST.get('action')
            if action=='process':
                process_job(obj)
                self.message_user(request,'Processing step finished. Review the current status, extracted rows and errors below.')
            elif action=='commit':
                if request.POST.get('reviewed')!='yes':
                    self.message_user(request,'Confirm that you reviewed the workbook before committing.',level='error')
                else:
                    obj,count=commit_job(obj.pk,request.user)
                    self.message_user(request,f'{obj.created_count} records created.' if obj.status=='completed' else 'This job cannot be committed. Review its errors/status.',level='success' if obj.status=='completed' else 'error')
            else:raise PermissionDenied('Unknown job action.')
            return redirect('admin:contentjob_review',job_id=obj.pk)
        if request.method!='GET':raise PermissionDenied()
        page=Paginator(obj.rows,20).get_page(request.GET.get('page'))
        return render(request,'admin/imports/contentjob/review.html',{**self.admin_site.each_context(request),'opts':self.model._meta,'title':f'Content job #{obj.pk} · {obj.get_kind_display()}',
            'job':obj,'page':page,'progress':round(obj.next_page*100/obj.page_count) if obj.page_count else 0,
            'can_process':obj.status in ['queued','failed'] or (obj.status=='review' and obj.kind!='pdf'),
            'can_commit':obj.status=='review' and obj.kind!='pdf'})

    def template_links(self,obj=None):
        from django.utils.html import format_html_join
        return format_html_join(' · ','<a href="{}">{} template.xlsx</a>',((reverse('admin:contentjob_template',args=[kind]),kind.title()) for kind in ['questions','exams','tests','materials']))
    def preview_rows(self,obj):return f'{len(obj.rows)} extracted/input rows. {len(obj.pages)} pages captured. Use Download Excel to review every row.'
    def download_link(self,obj):
        if not obj.pk:return 'Save the upload, select it in the list and choose Process uploads.'
        return format_html('<a href="{}">Download review workbook</a>',reverse('admin:contentjob_download',args=[obj.pk]))
    def template_view(self,request,kind):
        if kind not in COLUMNS or kind=='pdf':raise PermissionDenied()
        samples={
            'questions':[{'source_id':'example-001','kind':'PYQ','exam':'jee-advanced','year':'2024','subject':'Physics','chapter':'Units and Measurements','topic':'Dimensional analysis','subtopic':'Units and dimensions','difficulty':1,'question_type':'single_correct','stem':'Illustrative format only: What is the SI unit of force?','option_a':'newton','option_b':'joule','option_c':'watt','option_d':'pascal','answer':'A','solution':'Force is measured in newtons. Replace this row with a verified previous-year question.','source':'Replace with original paper URL','license':'Replace with source rights'}],
            'exams':[{'exam':'example-exam','name':'Example Exam','active':'false'}],
            'tests':[{'test_name':'Example physics test','exam':'jee-advanced','duration_minutes':30,'subject':'Physics','chapter':'Units and Measurements','topic':'Dimensional analysis','section':'Single correct','source_id':'test-example-001','question_type':'single_correct','stem':'What is the SI unit of force?','option_a':'newton','option_b':'joule','option_c':'watt','option_d':'pascal','answer':'A','correct_marks':3,'incorrect_marks':-1,'unanswered_marks':0,'partial_credit':'false','source':'Original example','license':'Original educational example'}],
            'materials':[{'document':'Example physics notes','exam':'jee-advanced','subject':'Physics','chapter':'Units and Measurements','topic':'Dimensional analysis','title':'Force and units','body':'The SI unit of force is the newton: one newton accelerates one kilogram at one metre per second squared.','page_reference':'1','source':'Original example','license':'Original educational example'}]}
        response=HttpResponse(workbook_bytes(kind,samples[kind]),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response['Content-Disposition']=f'attachment; filename="{kind}-template.xlsx"';return response
    def download_view(self,request,job_id):
        from django.shortcuts import get_object_or_404
        obj=get_object_or_404(self.get_queryset(request),pk=job_id)
        if not self.has_view_permission(request,obj):raise PermissionDenied()
        response=HttpResponse(workbook_bytes(obj.kind,obj.rows,obj.pages),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response['Content-Disposition']=f'attachment; filename="content-job-{obj.pk}.xlsx"';return response
    @admin.action(description='Process uploads / continue next 15 PDF pages (3 scanned pages per run)')
    def process_upload(self,request,queryset):
        from .job_services import process_job
        for obj in queryset:
            if not job_allowed(request.user,obj.tenant,obj.kind,obj.use_ocr):raise PermissionDenied()
            process_job(obj)
        self.message_user(request,'Processing step finished. Use Process & review on the job for progress and results.')
    @admin.action(description='Commit validated Excel uploads as drafts (after review)')
    def commit_upload(self,request,queryset):
        from django.shortcuts import redirect
        if queryset.count()!=1:
            self.message_user(request,'Select one job and open its review page to confirm the import.',level='error');return
        obj=queryset.first()
        if not job_allowed(request.user,obj.tenant,obj.kind,obj.use_ocr):raise PermissionDenied()
        return redirect('admin:contentjob_review',job_id=obj.pk)
    @admin.action(description='Download selected job as Excel (select one)')
    def download_excel(self,request,queryset):
        if queryset.count()!=1:self.message_user(request,'Select exactly one job.',level='error');return
        return self.download_view(request,queryset.first().pk)
