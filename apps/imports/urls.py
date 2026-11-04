from django.urls import path
from . import views

urlpatterns = [
    path("questions/csv/", views.question_csv_import, name="question_csv_import"),
    path("questions/csv/commit/", views.commit_question_csv, name="commit_question_csv"),
    path("questions/csv/template/", views.question_csv_template, name="question_csv_template"),
]
