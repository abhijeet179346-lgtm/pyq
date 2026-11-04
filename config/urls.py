from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView

urlpatterns = [
    # Browsers request /favicon.ico directly; nginx serves it in production.
    path("favicon.ico", RedirectView.as_view(url="/static/portal/favicon.ico", permanent=True)),
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
