from django.urls import path
from . import views

urlpatterns = [
    path("join/", views.join, name="tenant_join"),
    path("manage/access-codes/", views.access_code_export, name="access_code_export"),
    path("manage/teacher-subjects/", views.assign_teacher_subject, name="assign_teacher_subject"),
    path("learn/", views.practice_home, name="practice_home"),
    path("learn/questions/<int:question_id>/", views.practice_question, name="practice_question"),
    path("learn/questions/<int:question_id>/report/", views.report_question, name="report_question"),
    path("learn/questions/<int:question_id>/source-image/", views.question_source_image, name="question_source_image"),
    path("learn/questions/<int:question_id>/figures/<int:figure_index>/", views.question_figure_image, name="question_figure_image"),
]
