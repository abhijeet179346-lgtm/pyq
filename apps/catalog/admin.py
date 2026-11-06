from django.contrib import admin
from .models import Chapter, Exam, Paper, Subject, Topic, Subtopic


class PlatformCatalogAdmin(admin.ModelAdmin):
    def has_module_permission(self, request): return request.user.is_superuser
    def has_view_permission(self, request, obj=None): return request.user.is_superuser
    def has_add_permission(self, request): return request.user.is_superuser
    def has_change_permission(self, request, obj=None): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser


@admin.register(Topic)
class TopicAdmin(PlatformCatalogAdmin):
    list_display = ("name", "chapter")
    list_filter = ("chapter__subject", "chapter")
    search_fields = ("name", "chapter__name")


@admin.register(Subtopic)
class SubtopicAdmin(PlatformCatalogAdmin):
    list_display = ("name", "topic", "order")
    list_filter = ("topic__chapter__subject", "topic__chapter", "topic")
    search_fields = ("name", "topic__name", "topic__chapter__name")
    autocomplete_fields = ("topic",)


@admin.register(Exam)
class ExamAdmin(PlatformCatalogAdmin):
    list_display = ("name", "slug", "active", "subject_names")
    filter_horizontal = ("subjects",)

    @admin.display(description="Subjects")
    def subject_names(self, obj):
        return ", ".join(obj.subjects.values_list("name", flat=True))


for model in (Subject, Chapter, Paper):
    admin.site.register(model, PlatformCatalogAdmin)
