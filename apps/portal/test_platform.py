from urllib.parse import urlsplit

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.portal.services import create_handoff
from apps.tenancy.models import Tenant


class PlatformPortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Platform-regression-password-27"
        cls.user = User.objects.create_superuser(username="root", email="root@example.test",
            password=cls.password, role="platform_admin", email_verified_at=timezone.now())
        cls.tenant = Tenant.objects.create(name="Demo Institute", slug="demo")
        cls.student = User.objects.create_user(username="student", email="student@example.test",
            password=cls.password, tenant=cls.tenant, email_verified_at=timezone.now())

    def test_superuser_can_use_learner_pages_and_admin(self):
        from apps.features.models import FeatureEntitlement
        FeatureEntitlement.objects.update_or_create(tenant=Tenant.objects.get(is_master=True), key="tests",
                                                    defaults={"entitled": True, "enabled_override": True})
        client = Client(HTTP_HOST="localhost")
        response = client.post("/login/", {"email": self.user.email, "password": self.password}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/")
        self.assertContains(response, 'class="vault-admin-link" href="/admin/"')
        self.assertNotContains(response, "Enter your access code")
        for path in ("/practice/", "/practice/tests/", "/library/", "/progress/"):
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertEqual(client.get("/admin/").status_code, 200)

    def test_superuser_can_sign_in_on_institute_subdomain(self):
        client = Client(HTTP_HOST="demo.localhost")
        response = client.post("/login/", {"email": self.user.email, "password": self.password}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/admin/")
        self.assertContains(response, "Site administration")

    def test_platform_can_open_institute_with_own_superuser_identity(self):
        client = Client(HTTP_HOST="localhost")
        client.force_login(self.user)
        response = client.post("/admin/open-workspace/", {"tenant": self.tenant.pk})
        destination = urlsplit(response.url)
        self.assertEqual(destination.hostname, "demo.localhost")
        tenant_client = Client(HTTP_HOST="demo.localhost")
        response = tenant_client.get(destination.path + "?" + destination.query, follow=True)
        self.assertContains(response, "Site administration")
        self.assertEqual(int(tenant_client.session["_auth_user_id"]), self.user.pk)

    def test_normal_account_cannot_request_cross_tenant_handoff(self):
        other = Tenant.objects.create(name="Other Institute", slug="other")
        with self.assertRaises(ValueError):
            create_handoff(self.student, tenant=other)

    def test_unverified_superuser_still_requires_verification(self):
        self.user.email_verified_at = None
        self.user.save(update_fields=["email_verified_at"])
        client = Client(HTTP_HOST="localhost")
        client.force_login(self.user)
        self.assertRedirects(client.get("/"), "/verify-email/", fetch_redirect_response=False)
