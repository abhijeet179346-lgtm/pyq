from django.contrib import admin
from django.core.exceptions import PermissionDenied

from apps.content.models import Question
from apps.learning.models import Batch
from apps.learning.models import TeacherSubjectAssignment
from apps.tenancy.features import is_enabled
from apps.features.models import TenantExam
from .models import AttemptAnswer, TestAssignment, TestAttempt, TestQuestion, TestSection, TestTemplate


class TenantScopedAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser: return qs
        return qs.filter(tenant_id=getattr(request.user, "tenant_id", None)) if getattr(request.user, "tenant_id", None) else qs.none()
    def has_module_permission(self, request):
        return request.user.is_superuser or getattr(request.user, "role", None) == "tenant_admin"
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) is not None
            and (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None)))
    def has_add_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "tests"))
    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj) and (request.user.is_superuser or is_enabled(request.user.tenant, "tests"))
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


class TestSectionInline(admin.TabularInline):
    model = TestSection
    extra = 1


@admin.register(TestTemplate)
class TestTemplateAdmin(TenantScopedAdmin):
    list_display = ("name", "tenant", "duration_minutes", "active", "created_at")
    search_fields = ("name",)
    list_filter = ("active",)
    readonly_fields = ("created_by", "created_at")
    inlines = [TestSectionInline]
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "tenant" and not request.user.is_superuser:
            from apps.tenancy.models import Tenant
            kwargs["queryset"] = Tenant.objects.filter(pk=getattr(request.user, "tenant_id", None))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser or getattr(request.user, "role", None) == "tenant_admin" else qs.filter(created_by=request.user)
    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "tests"))
    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser: return True
        if getattr(request.user, "role", None) == "tenant_admin": return getattr(request.user, "tenant_id", None) is not None and (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None))
        return getattr(request.user, "role", None) == "teacher" and is_enabled(request.user.tenant, "tests") and (obj is None or (obj.tenant_id == getattr(request.user, "tenant_id", None) and obj.created_by_id == request.user.pk))
    def has_add_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "tests"))
    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj) and (request.user.is_superuser or is_enabled(request.user.tenant, "tests"))
    def save_model(self, request, obj, form, change):
        from apps.audit.models import AuditEvent
        before = {}
        if change:
            old = TestTemplate.objects.get(pk=obj.pk)
            before = {"name": old.name, "duration_minutes": old.duration_minutes, "active": old.active}
        if not request.user.is_superuser:
            obj.tenant_id = getattr(request.user, "tenant_id", None)
            if not change: obj.created_by = request.user
            if getattr(request.user, "role", None) == "teacher": obj.active = False
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(tenant=obj.tenant, actor=request.user, action="test_template_saved", object_type="TestTemplate",
            object_id=str(obj.pk), before=before, after={"name": obj.name, "duration_minutes": obj.duration_minutes, "active": obj.active})
        request._test_section_before = {section.pk: {"name": section.name, "order": section.order,
            "duration_minutes": section.duration_minutes, "question_count": section.question_count,
            "marking_scheme": section.marking_scheme} for section in obj.sections.all()}

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        before = getattr(request, "_test_section_before", {})
        for section in form.instance.sections.all():
            old = before.get(section.pk, {})
            after = {"name": section.name, "order": section.order, "duration_minutes": section.duration_minutes,
                     "question_count": section.question_count, "marking_scheme": section.marking_scheme}
            if old != after:
                from apps.audit.models import AuditEvent
                AuditEvent.objects.create(tenant=form.instance.tenant, actor=request.user, action="test_section_saved",
                    object_type="TestSection", object_id=str(section.pk), before=old, after=after)


@admin.register(TestSection)
class TestSectionAdmin(admin.ModelAdmin):
    list_display = ("name", "template", "order", "duration_minutes", "question_count")
    list_filter = ("template",)
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("template")
        if request.user.is_superuser or getattr(request.user, "role", None) == "tenant_admin": return qs if request.user.is_superuser else qs.filter(template__tenant_id=getattr(request.user, "tenant_id", None))
        return qs.filter(template__tenant_id=getattr(request.user, "tenant_id", None), template__created_by=request.user)
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.name == "template":
            kwargs["queryset"] = TestTemplate.objects.filter(tenant_id=getattr(request.user, "tenant_id", None))
            if getattr(request.user, "role", None) == "teacher": kwargs["queryset"] = kwargs["queryset"].filter(created_by=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    def has_module_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "tests"))
    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser: return True
        if getattr(request.user, "role", None) == "tenant_admin": return getattr(request.user, "tenant_id", None) is not None and (obj is None or obj.template.tenant_id == getattr(request.user, "tenant_id", None))
        return getattr(request.user, "role", None) == "teacher" and is_enabled(request.user.tenant, "tests") and (obj is None or (obj.template.tenant_id == getattr(request.user, "tenant_id", None) and obj.template.created_by_id == request.user.pk))
    def has_add_permission(self, request): return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "tests"))
    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj) and (request.user.is_superuser or is_enabled(request.user.tenant, "tests"))
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
    def save_model(self, request, obj, form, change):
        from apps.audit.models import AuditEvent
        before = {}
        if change:
            old = TestSection.objects.get(pk=obj.pk)
            before = {"name": old.name, "order": old.order, "duration_minutes": old.duration_minutes,
                      "question_count": old.question_count, "marking_scheme": old.marking_scheme}
        super().save_model(request, obj, form, change)
        after = {"name": obj.name, "order": obj.order, "duration_minutes": obj.duration_minutes,
                 "question_count": obj.question_count, "marking_scheme": obj.marking_scheme}
        if before != after:
            AuditEvent.objects.create(tenant=obj.template.tenant, actor=request.user, action="test_section_saved",
                object_type="TestSection", object_id=str(obj.pk), before=before, after=after)


@admin.register(TestQuestion)
class TestQuestionAdmin(admin.ModelAdmin):
    list_display = ("section", "question", "order", "points_override")
    list_filter = ("section__template",)
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("section__template")
        if request.user.is_superuser: return qs
        if getattr(request.user, "role", None) == "tenant_admin": return qs.filter(section__template__tenant_id=getattr(request.user, "tenant_id", None))
        return qs.filter(section__template__tenant_id=getattr(request.user, "tenant_id", None), section__template__created_by=request.user)
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.name == "section":
            kwargs["queryset"] = TestSection.objects.filter(template__tenant_id=getattr(request.user, "tenant_id", None))
            if getattr(request.user, "role", None) == "teacher": kwargs["queryset"] = kwargs["queryset"].filter(template__created_by=request.user)
        if not request.user.is_superuser and db_field.name == "question":
            allowed = Question.objects.none()
            if is_enabled(request.user.tenant, "tenant_question_bank"):
                allowed = Question.objects.filter(tenant_id=getattr(request.user, "tenant_id", None), kind=Question.Kind.PRACTICE,
                                                  status=Question.Status.PUBLISHED)
                if getattr(request.user, "role", None) == "teacher":
                    subject_ids = TeacherSubjectAssignment.objects.filter(tenant_id=getattr(request.user, "tenant_id", None),
                        teacher=request.user).values_list("subject_id", flat=True)
                    allowed = allowed.filter(topic__chapter__subject_id__in=subject_ids)
            if is_enabled(request.user.tenant, "global_pyq_bank"):
                global_questions = Question.objects.filter(tenant__isnull=True, kind=Question.Kind.PYQ,
                    status=Question.Status.PUBLISHED, paper__exam_id__in=TenantExam.objects.filter(
                        tenant_id=getattr(request.user, "tenant_id", None), enabled=True).values_list("exam_id", flat=True))
                if getattr(request.user, "role", None) == "teacher":
                    subject_ids = TeacherSubjectAssignment.objects.filter(tenant_id=getattr(request.user, "tenant_id", None),
                        teacher=request.user).values_list("subject_id", flat=True)
                    global_questions = global_questions.filter(topic__chapter__subject_id__in=subject_ids)
                allowed = allowed | global_questions
            kwargs["queryset"] = allowed
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    def has_module_permission(self, request): return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "tests"))
    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser: return True
        if getattr(request.user, "role", None) == "tenant_admin": return getattr(request.user, "tenant_id", None) is not None and (obj is None or obj.section.template.tenant_id == getattr(request.user, "tenant_id", None))
        return getattr(request.user, "role", None) == "teacher" and is_enabled(request.user.tenant, "tests") and (obj is None or (obj.section.template.tenant_id == getattr(request.user, "tenant_id", None) and obj.section.template.created_by_id == request.user.pk))
    def has_add_permission(self, request): return request.user.is_superuser or (getattr(request.user, "role", None) in ("tenant_admin", "teacher") and is_enabled(request.user.tenant, "tests"))
    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj) and (request.user.is_superuser or is_enabled(request.user.tenant, "tests"))
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser
    def save_model(self, request, obj, form, change):
        from apps.audit.models import AuditEvent
        before = {}
        if change:
            old = TestQuestion.objects.get(pk=obj.pk)
            before = {"section_id": old.section_id, "question_id": old.question_id,
                      "order": old.order, "points_override": str(old.points_override) if old.points_override is not None else None}
        super().save_model(request, obj, form, change)
        after = {"section_id": obj.section_id, "question_id": obj.question_id,
                 "order": obj.order, "points_override": str(obj.points_override) if obj.points_override is not None else None}
        AuditEvent.objects.create(tenant=obj.section.template.tenant, actor=request.user, action="test_question_saved",
            object_type="TestQuestion", object_id=str(obj.pk), before=before, after=after)


@admin.register(TestAssignment)
class TestAssignmentAdmin(TenantScopedAdmin):
    list_display = ("title", "batch", "template", "available_from", "deadline", "active")
    list_filter = ("active", "batch")
    readonly_fields = ("assigned_by", "created_at")
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            if db_field.name == "tenant":
                from apps.tenancy.models import Tenant
                kwargs["queryset"] = Tenant.objects.filter(pk=getattr(request.user, "tenant_id", None))
            if db_field.name == "batch": kwargs["queryset"] = Batch.objects.filter(tenant_id=getattr(request.user, "tenant_id", None))
            if db_field.name == "template": kwargs["queryset"] = TestTemplate.objects.filter(tenant_id=getattr(request.user, "tenant_id", None))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
    def save_model(self, request, obj, form, change):
        from apps.audit.models import AuditEvent
        before = {}
        if change:
            old = TestAssignment.objects.get(pk=obj.pk)
            before = {"title": old.title, "batch_id": old.batch_id, "template_id": old.template_id,
                      "available_from": old.available_from.isoformat(), "deadline": old.deadline.isoformat(), "active": old.active}
        if not request.user.is_superuser:
            if not is_enabled(request.user.tenant, "custom_test_assignment"):
                raise PermissionDenied("Custom test assignment is disabled for this institute.")
            if obj.batch.tenant_id != getattr(request.user, "tenant_id", None) or obj.template.tenant_id != getattr(request.user, "tenant_id", None):
                raise PermissionDenied("Assignment must use this tenant's batch and test template.")
            obj.tenant_id = getattr(request.user, "tenant_id", None)
            obj.assigned_by = request.user
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(tenant=obj.tenant, actor=request.user, action="test_assignment_saved", object_type="TestAssignment",
            object_id=str(obj.pk), before=before, after={"title": obj.title, "batch_id": obj.batch_id,
            "template_id": obj.template_id, "available_from": obj.available_from.isoformat(),
            "deadline": obj.deadline.isoformat(), "active": obj.active})
    def has_add_permission(self, request):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and
            is_enabled(request.user.tenant, "tests") and is_enabled(request.user.tenant, "custom_test_assignment"))
    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj) and (request.user.is_superuser or
            (is_enabled(request.user.tenant, "tests") and is_enabled(request.user.tenant, "custom_test_assignment")))


@admin.register(TestAttempt)
class TestAttemptAdmin(admin.ModelAdmin):
    list_display = ("assignment", "student", "status", "total_score", "started_at", "submitted_at")
    list_filter = ("status", "assignment")
    readonly_fields = tuple(field.name for field in TestAttempt._meta.fields)
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("assignment")
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))
    def has_module_permission(self, request): return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "tests"))
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) is not None
            and (obj is None or obj.tenant_id == getattr(request.user, "tenant_id", None)))
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(AttemptAnswer)
class AttemptAnswerAdmin(admin.ModelAdmin):
    list_display = ("attempt", "question", "is_correct", "marked_for_review", "time_spent_seconds")
    readonly_fields = tuple(field.name for field in AttemptAnswer._meta.fields)
    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("attempt")
        return qs if request.user.is_superuser else qs.filter(attempt__tenant_id=getattr(request.user, "tenant_id", None))
    def has_module_permission(self, request): return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and is_enabled(request.user.tenant, "tests"))
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) is not None
            and (obj is None or obj.attempt.tenant_id == getattr(request.user, "tenant_id", None)))
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


from .models import TimedTest,TimedAttempt

@admin.register(TimedTest)
class TimedTestAdmin(TenantScopedAdmin):
    list_display=('name','tenant','exam','series','paper_number','subject','duration_minutes','fullscreen_exit_limit','active')
    list_filter=('tenant','exam','series','subject','active')
    search_fields=('name','series')
    def save_model(self,request,obj,form,change):
        if not request.user.is_superuser:obj.tenant=request.user.tenant
        super().save_model(request,obj,form,change)
    def formfield_for_foreignkey(self,db_field,request,**kwargs):
        if db_field.name=='tenant' and not request.user.is_superuser:
            from apps.tenancy.models import Tenant
            kwargs['queryset']=Tenant.objects.filter(pk=request.user.tenant_id)
        return super().formfield_for_foreignkey(db_field,request,**kwargs)

@admin.register(TimedAttempt)
class TimedAttemptAdmin(admin.ModelAdmin):
    list_display=('test','user','status','started_at','deadline','score','proctoring_events')
    readonly_fields=tuple(field.name for field in TimedAttempt._meta.fields)
    @admin.display(description='Full-screen exits / tab switches')
    def proctoring_events(self,obj):
        exits=sum(1 for event in obj.events if event.get('type')=='fullscreen_exit')
        return f"{exits} / {len(obj.events)-exits}" if obj.events else '—'
    def get_queryset(self,request):
        qs=super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(test__tenant_id=getattr(request.user,'tenant_id',None))
    def has_module_permission(self,request):return request.user.is_superuser or getattr(request.user,'role',None)=='tenant_admin'
    def has_view_permission(self,request,obj=None):return self.has_module_permission(request) and (obj is None or request.user.is_superuser or obj.test.tenant_id==request.user.tenant_id)
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False
