from django.contrib.auth.views import LogoutView
from django.urls import path
from . import views, oauth, direct
from .views import home, PortalLoginView

urlpatterns = [
    path("", home, name="portal_home"),
    path("practice/start/", direct.start_practice, name="start_practice"),
    path("practice/sessions/<uuid:session_id>/<int:position>/", direct.practice_session, name="practice_session"),
    path("practice/sessions/<uuid:session_id>/", direct.practice_summary, name="practice_summary"),
    path("practice/", direct.question_bank, name="direct_bank"),
    path("practice/questions/<int:question_id>/", direct.question, name="direct_question"),
    path("practice/questions/<int:question_id>/figures/<int:index>/", direct.question_figure, name="direct_question_figure"),
    path("progress/", direct.progress, name="direct_progress"),
    path("library/<int:section_id>/source/", direct.study_source, name="direct_study_source"),
    path("library/<int:section_id>/", direct.study_section, name="direct_study_section"),
    path("library/", direct.library, name="direct_library"),
    path("login/", PortalLoginView.as_view(), name="portal_login"),
    path("logout/", LogoutView.as_view(next_page="portal_home"), name="portal_logout"),
    path("signup/", views.signup, name="portal_signup"),
    path("activate/", views.activate, name="activate_account"),
    path("verify-email/", views.verify_email, name="verify_email"),
    path("verify-email/resend/", views.resend_verification, name="resend_verification"),
    path("verify-email/confirm/<str:token>/", views.verify_email_confirm, name="verify_email_confirm"),
    path("admin/open-workspace/", views.open_workspace, name="open_workspace"),
    path("auth/continue/", views.continue_login, name="continue_login"),
    path("auth/google/start/", oauth.google_start, name="google_login"),
    path("auth/google/callback/", oauth.google_callback, name="google_callback"),
]
