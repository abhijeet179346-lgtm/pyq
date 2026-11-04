from django.utils import timezone
from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from apps.features.models import FeatureEntitlement
from apps.tenancy.features import FEATURES
from apps.tenancy.models import Tenant
from .models import User


class AdminAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Local-regression-password-27"
        cls.tenant = Tenant.objects.create(name="First Institute", slug="first")
        cls.other_tenant = Tenant.objects.create(name="Second Institute", slug="second")
        cls.admin_user = User.objects.create_user(
            username="first-admin", email="admin@first.test", password=cls.password,
            tenant=cls.tenant, role=User.Role.TENANT_ADMIN, is_staff=True, email_verified_at=timezone.now(),
        )
        cls.platform_user = User.objects.create_superuser(
            username="platform", email="platform@example.test", password=cls.password,
            role=User.Role.PLATFORM_ADMIN, email_verified_at=timezone.now(),
        )
        FeatureEntitlement.objects.bulk_create([
            FeatureEntitlement(tenant=cls.tenant, key=key, entitled=True)
            for key in FEATURES
        ])

    def test_anonymous_login_pages_render_on_each_host(self):
        for host in ("first.localhost", "localhost", "unknown.localhost"):
            with self.subTest(host=host):
                response = self.client.get("/admin/login/?next=/admin/", HTTP_HOST=host)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'name="username"')
                self.assertContains(response, 'name="password"')

    def test_anonymous_admin_permissions_deny_access_without_errors(self):
        request = RequestFactory().get("/admin/login/")
        request.user = AnonymousUser()
        request.tenant = self.tenant
        for model, model_admin in admin.site._registry.items():
            for hook in ("has_module_permission", "has_view_permission", "has_add_permission",
                         "has_change_permission", "has_delete_permission"):
                with self.subTest(model=model._meta.label, hook=hook):
                    self.assertFalse(getattr(model_admin, hook)(request))

    def test_anonymous_protected_pages_redirect_to_login(self):
        for path in ("/admin/", "/learn/", "/tests/", "/tutor/", "/manage/scans/"):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST="first.localhost")
                self.assertEqual(response.status_code, 302)
                self.assertIn("/admin/login/" if path == "/admin/" else "/login/", response.url)

    def test_tenant_admin_can_sign_in_by_email_and_open_dashboard(self):
        response = self.client.post("/admin/login/?next=/admin/", {
            "username": self.admin_user.email, "password": self.password, "next": "/admin/",
        }, HTTP_HOST="first.localhost", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.admin_user.pk)
        self.assertContains(response, "Site administration")

    def test_invalid_password_renders_error(self):
        response = self.client.post("/admin/login/", {
            "username": self.admin_user.email, "password": "wrong-password",
        }, HTTP_HOST="first.localhost")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "Please enter the correct")

    def test_tenant_credentials_are_rejected_on_other_hosts(self):
        for host in ("second.localhost", "localhost", "unknown.localhost"):
            with self.subTest(host=host):
                client = Client(HTTP_HOST=host)
                response = client.post("/admin/login/", {
                    "username": self.admin_user.email, "password": self.password,
                })
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("_auth_user_id", client.session)

    def test_csrf_token_is_required_for_login(self):
        client = Client(enforce_csrf_checks=True, HTTP_HOST="first.localhost")
        response = client.post("/admin/login/", {
            "username": self.admin_user.email, "password": self.password,
        })
        self.assertEqual(response.status_code, 403)

    def test_platform_admin_can_sign_in_on_platform_host(self):
        response = self.client.post("/admin/login/?next=/admin/", {
            "username": self.platform_user.username, "password": self.password,
            "next": "/admin/",
        }, HTTP_HOST="localhost", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.platform_user.pk)

    def test_registered_admin_lists_and_add_forms_render(self):
        for user, host in ((self.platform_user, "localhost"), (self.admin_user, "first.localhost")):
            self.client.force_login(user)
            request = RequestFactory().get("/admin/")
            request.user = user
            request.tenant = user.tenant
            for model, model_admin in admin.site._registry.items():
                label = f"{model._meta.app_label}_{model._meta.model_name}"
                for action, permitted in (
                    ("changelist", model_admin.has_view_permission(request)),
                    ("add", model_admin.has_add_permission(request)),
                ):
                    if permitted:
                        with self.subTest(user=user.username, model=label, action=action):
                            response = self.client.get(reverse(f"admin:{label}_{action}"), HTTP_HOST=host)
                            self.assertEqual(response.status_code, 200)
            self.client.logout()
