from django.urls import path
from . import views

urlpatterns = [
    path("tutor/", views.tutor_home, name="tutor_home"),
    path("tutor/photo/", views.tutor_photo, name="tutor_photo"),
    path("tutor/threads/<int:thread_id>/", views.tutor_thread, name="tutor_thread"),
    path("tutor/messages/<int:message_id>/feedback/", views.tutor_feedback, name="tutor_feedback"),
    path("tutor/threads/<int:thread_id>/escalate/", views.escalate_thread, name="tutor_escalate"),
    path("tutor/mistakes/<str:source>/<int:attempt_id>/", views.mistake_help, name="mistake_help"),
    path("learn/mastery/", views.mastery_home, name="mastery_home"),
    path("manage/batches/<int:batch_id>/tutor-analytics/", views.teacher_tutor_analytics, name="teacher_tutor_analytics"),
]
