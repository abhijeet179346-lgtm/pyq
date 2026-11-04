from django.contrib import admin
from .models import DirectPracticeAttempt


@admin.register(DirectPracticeAttempt)
class DirectPracticeAttemptAdmin(admin.ModelAdmin):
    list_display = ("user", "question", "is_correct", "time_taken_seconds", "submitted_at")
    list_filter = ("is_correct", "user__tenant", "question__paper__exam")
    search_fields = ("user__email", "question__stem")
    readonly_fields = tuple(field.name for field in DirectPracticeAttempt._meta.fields)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
