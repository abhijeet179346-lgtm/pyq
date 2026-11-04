from django.urls import path
from .views import tenant_branding

urlpatterns = [path("manage/branding/", tenant_branding, name="tenant_branding")]
