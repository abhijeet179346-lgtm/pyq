from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from apps.tenancy.features import is_enabled
from apps.audit.models import AuditEvent
from apps.learning.models import TeacherSubjectAssignment
from .models import ContentReport, Question, QuestionConceptTag, QuestionRevision


class TenantScopedAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser: return qs
        if getattr(request.user, "tenant_id", None) is None: return qs.none()
        return qs.filter(tenant_id=getattr(request.user, "tenant_id", None))

    def save_model(self, request, obj, form, change):
        before = {}
        previous = None
        if change:
            previous = Question.objects.get(pk=obj.pk)
            before = {"stem": previous.stem, "options": previous.options, "answer": previous.answer,
                      "solution": previous.solution, "difficulty": previous.difficulty,
                      "topic_id": previous.topic_id, "subtopic_id": previous.subtopic_id, "status": previous.status, "version": previous.version}
        if not request.user.is_superuser:
            obj.tenant_id = getattr(request.user, "tenant_id", None)
            obj.kind = Question.Kind.PRACTICE
            obj.paper = None
            if not is_enabled(request.user.tenant, "tenant_question_bank"):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied("Tenant question bank is disabled.")
            if obj.ai_generated and obj.status == Question.Status.PUBLISHED:
                obj.reviewed_by = request.user
        if change:
            obj.version = previous.version + 1
        else:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if previous:
            QuestionRevision.objects.create(question=obj, version=previous.version, snapshot=before, edited_by=request.user)
        AuditEvent.objects.create(tenant=obj.tenant, actor=request.user, action="question_saved",
                                  object_type="Question", object_id=str(obj.pk), before=before,
                                  after={"stem": obj.stem, "options": obj.options, "answer": obj.answer,
                                         "solution": obj.solution, "difficulty": obj.difficulty,
                                         "topic_id": obj.topic_id, "subtopic_id": obj.subtopic_id, "status": obj.status, "version": obj.version})

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            fields += ["kind", "paper", "version", "ai_generated", "reviewed_by", "source_scan_id", "source_page"]
        return fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "tenant" and not request.user.is_superuser:
            from apps.tenancy.models import Tenant
            kwargs["queryset"] = Tenant.objects.filter(pk=getattr(request.user, "tenant_id", None))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_delete_permission(self, request, obj=None): return False

    def _tenant_editor(self, request, obj=None):
        return (request.user.is_superuser or
                (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and getattr(request.user, "tenant_id", None) is not None
                 and (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None))
                 and is_enabled(request.user.tenant, "tenant_question_bank")))

    def has_view_permission(self, request, obj=None): return self._tenant_editor(request, obj)
    def has_add_permission(self, request): return self._tenant_editor(request)
    def has_change_permission(self, request, obj=None): return self._tenant_editor(request, obj)


class QuestionMaterialLinkInline(admin.TabularInline):
    from apps.materials.models import QuestionMaterialLink
    model=QuestionMaterialLink
    extra=0
    def has_view_permission(self,request,obj=None):
        return request.user.is_superuser or (getattr(request.user,'role',None) in ('tenant_admin','teacher') and is_enabled(request.user.tenant,'study_material') and (obj is None or obj.tenant_id==request.user.tenant_id))
    def has_add_permission(self,request,obj=None):return self.has_view_permission(request,obj)
    def has_change_permission(self,request,obj=None):return self.has_view_permission(request,obj)
    def has_delete_permission(self,request,obj=None):return self.has_view_permission(request,obj)
    def formfield_for_foreignkey(self,db_field,request,**kwargs):
        if db_field.name=='section':
            from apps.materials.models import MaterialSection
            from django.db.models import Q
            queryset=MaterialSection.objects.filter(active=True,document__active=True)
            if not request.user.is_superuser:queryset=queryset.filter(Q(document__tenant_id=request.user.tenant_id)|Q(document__tenant__isnull=True))
            kwargs['queryset']=queryset
        return super().formfield_for_foreignkey(db_field,request,**kwargs)


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    widget = MultipleImageInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        return [super(MultipleImageField, self).clean(item, initial) for item in files]


class QuestionAdminForm(forms.ModelForm):
    add_figures = MultipleImageField(required=False, label="Add images",
        help_text="Diagrams, graphs or tables the question needs. PNG, JPEG, WEBP or GIF, up to 5 MB each.",
        widget=MultipleImageInput(attrs={"accept": "image/png,image/jpeg,image/webp,image/gif"}))
    remove_figures = forms.TypedMultipleChoiceField(required=False, coerce=int, label="Remove images",
        widget=forms.CheckboxSelectMultiple)
    figure_order = forms.CharField(required=False, label="Image order",
        help_text="Existing image numbers in display order, separated by commas. Removed images may be omitted.")

    class Meta:
        model = Question
        exclude = ("figure_paths",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        paths = self.instance.figure_paths if self.instance and self.instance.pk else []
        self.fields["remove_figures"].choices = [(index, f"Image {index + 1}") for index in range(len(paths))]
        if paths:
            self.fields['figure_order'].initial = ', '.join(str(index + 1) for index in range(len(paths)))
        if not paths:
            self.fields["remove_figures"].widget = forms.HiddenInput()
            self.fields['figure_order'].widget = forms.HiddenInput()

    def clean(self):
        data = super().clean()
        paths = self.instance.figure_paths or [] if self.instance.pk else []
        removed = set(data.get('remove_figures') or [])
        raw = (data.get('figure_order') or '').strip()
        if raw:
            try:
                order = [int(part.strip()) - 1 for part in raw.split(',')]
            except ValueError:
                self.add_error('figure_order', 'Use comma-separated image numbers.')
                return data
            expected = set(range(len(paths))) - removed
            if len(order) == len(paths) and set(order) == set(range(len(paths))):
                order = [index for index in order if index not in removed]
            if set(order) != expected or len(order) != len(expected):
                self.add_error('figure_order', 'List every image you are keeping exactly once.')
            else:
                data['parsed_figure_order'] = order
        else:
            data['parsed_figure_order'] = [index for index in range(len(paths)) if index not in removed]
        return data

    def clean_add_figures(self):
        from .figures import MAX_FIGURES, clean_image
        files = self.cleaned_data.get("add_figures") or []
        kept = len(self.instance.figure_paths or []) - len(self.data.getlist("remove_figures")) if self.instance.pk else 0
        if kept + len(files) > MAX_FIGURES:
            raise forms.ValidationError(f"A question can have at most {MAX_FIGURES} images.")
        cleaned = []
        for upload in files:
            cleaned.append(clean_image(upload.read(), upload.name))
        return cleaned


@admin.register(Question)
class QuestionAdmin(TenantScopedAdmin):
    form = QuestionAdminForm
    inlines=[QuestionMaterialLinkInline]
    list_display = ("id", "kind", "tenant", "question_type", "topic", "subtopic", "difficulty", "status", "ai_generated", "version")
    list_filter = ("kind", "status", "question_type", "difficulty", "tenant", "exam", "source_year", "paper__exam", "paper__year", "topic__chapter__subject", "topic__chapter", "topic", "subtopic")
    search_fields = ("stem", "source", "topic__name", "subtopic__name", "tags")
    readonly_fields = ("version", "created_at", "updated_at", "figure_previews")

    def get_urls(self):
        return [path("<int:question_id>/figures/<int:index>/", self.admin_site.admin_view(self.figure_view),
                     name="content_question_figure")] + super().get_urls()

    def figure_view(self, request, question_id, index):
        from .figures import content_type, open_figure
        question = get_object_or_404(self.get_queryset(request), pk=question_id)
        if not self.has_view_permission(request, question):
            raise PermissionDenied()
        if not 0 <= index < len(question.figure_paths):
            raise Http404()
        handle = open_figure(question.figure_paths[index])
        if handle is None:
            raise Http404()
        return FileResponse(handle, content_type=content_type(question.figure_paths[index]))

    @admin.display(description="Images")
    def figure_previews(self, obj):
        if not obj or not obj.pk or not obj.figure_paths:
            return "No images. Use “Add images” to attach diagrams."
        return format_html_join("", '<figure style="display:inline-block;margin:0 12px 12px 0"><img src="{}" alt="Image {}" style="max-height:160px;max-width:260px;border:1px solid var(--border-color,#ccc);border-radius:6px"><figcaption>Image {}</figcaption></figure>',
            ((reverse("admin:content_question_figure", args=[obj.pk, index]), index + 1, index + 1) for index in range(len(obj.figure_paths))))

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .figures import store
        removed = set(form.cleaned_data.get("remove_figures") or [])
        added = form.cleaned_data.get("add_figures") or []
        order = form.cleaned_data.get('parsed_figure_order', [])
        if removed or added or order != list(range(len(obj.figure_paths or []))):
            paths = [obj.figure_paths[index] for index in order]
            paths += [store(data, extension, obj.pk) for data, extension in added]
            Question.objects.filter(pk=obj.pk).update(figure_paths=paths)
            obj.figure_paths = paths

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser and getattr(request.user, "tenant_id", None):
            qs = qs.filter(kind=Question.Kind.PRACTICE)
            if getattr(request.user, "role", None) == "teacher":
                subject_ids = TeacherSubjectAssignment.objects.filter(tenant_id=getattr(request.user, "tenant_id", None),
                    teacher=request.user).values_list("subject_id", flat=True)
                qs = qs.filter(topic__chapter__subject_id__in=subject_ids)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and getattr(request.user, "role", None) == "teacher" and db_field.name == "topic":
            subject_ids = TeacherSubjectAssignment.objects.filter(tenant_id=getattr(request.user, "tenant_id", None),
                teacher=request.user).values_list("subject_id", flat=True)
            from apps.catalog.models import Topic
            kwargs["queryset"] = Topic.objects.filter(chapter__subject_id__in=subject_ids)
        if not request.user.is_superuser and getattr(request.user, "role", None) == "teacher" and db_field.name == "subtopic":
            from apps.catalog.models import Subtopic
            subject_ids = TeacherSubjectAssignment.objects.filter(tenant_id=getattr(request.user, "tenant_id", None), teacher=request.user).values_list("subject_id", flat=True)
            kwargs["queryset"] = Subtopic.objects.filter(topic__chapter__subject_id__in=subject_ids)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_module_permission(self, request):
        return request.user.is_superuser or bool(getattr(request.user, "tenant_id", None) and is_enabled(request.user.tenant, "tenant_question_bank"))


@admin.register(QuestionRevision)
class QuestionRevisionAdmin(admin.ModelAdmin):
    list_display = ("question", "version", "edited_by", "created_at")
    readonly_fields = ("question", "version", "snapshot", "edited_by", "created_at")
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(question__tenant_id=getattr(request.user, "tenant_id", None))
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or bool(getattr(request.user, "tenant_id", None) and
            (obj is None or obj.question.tenant_id == getattr(request.user, "tenant_id", None)))


class PlatformOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request): return request.user.is_superuser
    def has_view_permission(self, request, obj=None): return request.user.is_superuser
    def has_add_permission(self, request): return request.user.is_superuser
    def has_change_permission(self, request, obj=None): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


admin.site.register(QuestionConceptTag, PlatformOnlyAdmin)


@admin.register(ContentReport)
class ContentReportAdmin(admin.ModelAdmin):
    list_display = ("question", "tenant", "reporter", "created_at")
    readonly_fields = ("question", "tenant", "reporter", "reason", "created_at")
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_module_permission(self, request):
        return request.user.is_superuser or bool(getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "question_reporting"))
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
