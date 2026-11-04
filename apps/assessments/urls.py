from django.urls import path
from . import views,timed_views

urlpatterns = [
    path("practice/tests/",timed_views.catalog,name="timed_catalog"),
    path("practice/tests/<int:test_id>/start/",timed_views.start,name="timed_start"),
    path("practice/test-attempts/<int:attempt_id>/",timed_views.attempt,name="timed_attempt"),
    path("practice/test-attempts/<int:attempt_id>/result/",timed_views.result,name="timed_result"),
    path("tests/", views.assignment_list, name="test_assignment_list"),
    path("tests/assignments/<int:assignment_id>/start/", views.start_assignment, name="start_test_assignment"),
    path("tests/attempts/<int:attempt_id>/", views.test_attempt, name="test_attempt"),
    path("tests/attempts/<int:attempt_id>/result/", views.test_result, name="test_result"),
    path("manage/batches/<int:batch_id>/results/", views.batch_results, name="batch_results"),
    path("manage/batches/<int:batch_id>/results.csv", views.batch_results_export, name="batch_results_export"),
]
