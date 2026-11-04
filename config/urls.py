from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("apps.portal.urls")),
    path("admin/", admin.site.urls),
    path("imports/", include("apps.imports.urls")),
    path("", include("apps.learning.urls")),
    path("", include("apps.assessments.urls")),
    path("", include("apps.tenancy.urls")),
    path("", include("apps.materials.urls")),
    path("", include("apps.tutoring.urls")),
    path("", include("apps.scans.urls")),
]
