from django.urls import path
from . import views

urlpatterns = [
    path("study/topics/<int:topic_id>/", views.ncert_studio, name="ncert_studio"),
    path("teach/batches/<int:batch_id>/projector/", views.projector_mode, name="projector_mode"),
]
