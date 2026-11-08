from django.contrib import admin
from .models import DirectPracticeAttempt, StudentFeedback


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


@admin.register(StudentFeedback)
class StudentFeedbackAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tenant", "category", "reporter", "question", "status", "notification_sent_at")
    list_filter = ("status", "category", "tenant")
    search_fields = ("message", "reporter__username", "reporter__email")
    readonly_fields = ("tenant", "reporter", "category", "message", "page_path", "question", "created_at", "notification_sent_at")
    actions = ("retry_notifications",)

    @admin.action(description="Retry pending email notifications")
    def retry_notifications(self, request, queryset):
        from .feedback_notifications import notify_feedback
        sent = sum(notify_feedback(item) for item in queryset.filter(notification_sent_at__isnull=True))
        self.message_user(request, f"Sent {sent} pending feedback notification(s).")

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("tenant", "reporter", "question")
        return qs if request.user.is_superuser else qs.filter(tenant_id=getattr(request.user, "tenant_id", None))

    def has_module_permission(self, request):
        # Logged-out visitors (e.g. the admin login page) have no role or tenant.
        return request.user.is_superuser or (getattr(request.user, "role", None) == "tenant_admin" and getattr(request.user, "tenant_id", None) is not None)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and (obj is None or request.user.is_superuser or obj.tenant_id == getattr(request.user, "tenant_id", None))

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
