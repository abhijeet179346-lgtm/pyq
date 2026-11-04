from django.urls import path
from . import views

urlpatterns = [
    path("manage/scans/", views.scan_import_list, name="scan_import_list"),
    path("manage/scans/<int:scan_id>/", views.scan_import_detail, name="scan_import_detail"),
    path("manage/scans/<int:scan_id>/pages/<int:page_number>/", views.scan_page_image, name="scan_page_image"),
]
