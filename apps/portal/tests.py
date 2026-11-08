from django.utils import timezone
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.features.models import FeatureEntitlement
from apps.learning.models import Batch, BatchEnrollment, TeacherBatchAssignment
from apps.tenancy.models import Tenant


class PortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Portal-regression-password-27"
        cls.tenant = Tenant.objects.create(name="Learning Institute", slug="learning")
        cls.other = Tenant.objects.create(name="Other Institute", slug="other")
        cls.student = User.objects.create_user(username="student", email="student@example.test",
            password=cls.password, tenant=cls.tenant, role="student", is_staff=False, email_verified_at=timezone.now())
        cls.teacher = User.objects.create_user(username="teacher", email="teacher@example.test",
            password=cls.password, tenant=cls.tenant, role="teacher", email_verified_at=timezone.now())
        cls.batch = Batch.objects.create(tenant=cls.tenant, name="Visible batch")
        cls.hidden_batch = Batch.objects.create(tenant=cls.tenant, name="Unassigned batch")
        Batch.objects.create(tenant=cls.other, name="Other tenant batch")
        BatchEnrollment.objects.create(tenant=cls.tenant, batch=cls.batch, student=cls.student)
        TeacherBatchAssignment.objects.create(tenant=cls.tenant, batch=cls.batch, teacher=cls.teacher)
        for key in ("practice", "tenant_question_bank", "tests", "custom_test_assignment", "batch_management"):
            FeatureEntitlement.objects.create(tenant=cls.tenant, key=key, entitled=True)

    def setUp(self):
        self.client = Client(HTTP_HOST="learning.localhost")

    def test_root_is_a_public_main_application_entry(self):
        response = self.client.get("/")
        self.assertContains(response, "Continue with Google")
        self.assertContains(response, 'name="password"')
        self.assertContains(response, reverse("portal_login"))
        self.assertNotContains(response, "Site administration")

    def test_nonstaff_student_can_login_and_land_on_dashboard(self):
        response = self.client.post("/login/", {
            "email": self.student.email, "password": self.password,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], "/")
        self.assertContains(response, "Your personal learning workspace")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.student.pk)

    def test_student_dashboard_shows_only_enrolled_batches(self):
        self.client.force_login(self.student)
        response = self.client.get("/")
        self.assertContains(response, "Visible batch")
        self.assertNotContains(response, "Unassigned batch")
        self.assertNotContains(response, "Other tenant batch")
        self.assertNotContains(response, "Invite your batch")

    def test_teacher_dashboard_shows_only_assigned_batches(self):
        self.client.force_login(self.teacher)
        response = self.client.get("/")
        self.assertContains(response, "Your classes")
        self.assertContains(response, "Visible batch")
        self.assertNotContains(response, "Unassigned batch")

    def test_dashboard_hides_features_that_are_not_enabled(self):
        self.client.force_login(self.student)
        response = self.client.get("/")
        self.assertContains(response, f'href="{reverse("direct_bank")}"')
        self.assertNotContains(response, reverse("tutor_home"))
        self.assertNotContains(response, reverse("mastery_home"))
        FeatureEntitlement.objects.filter(tenant=self.tenant, key="practice").update(enabled_override=False)
        response = self.client.get("/")
        self.assertNotContains(response, f'href="{reverse("direct_bank")}"')
        self.assertEqual(self.client.get("/learn/").status_code, 403)

    def test_portal_preserves_safe_next_url(self):
        response = self.client.post("/login/?next=/tests/", {
            "email": self.student.email, "password": self.password, "next": "/tests/",
        })
        self.assertRedirects(response, "/tests/", fetch_redirect_response=False)

    def test_portal_rejects_external_redirect(self):
        response = self.client.post("/login/", {
            "email": self.student.email, "password": self.password,
            "next": "https://untrusted.example/",
        })
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_portal_rejects_credentials_on_another_tenant(self):
        client = Client(HTTP_HOST="other.localhost")
        response = client.post("/login/", {
            "email": self.student.email, "password": self.password,
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", client.session)

    def test_dashboard_rejects_session_from_another_tenant(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get("/", HTTP_HOST="other.localhost").status_code, 403)

    def test_unknown_host_has_no_tenant_portal(self):
        self.assertEqual(self.client.get("/", HTTP_HOST="unknown.localhost").status_code, 404)
        self.assertEqual(self.client.get("/login/", HTTP_HOST="unknown.localhost").status_code, 404)

    def test_logout_requires_post_and_ends_session(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        response = self.client.post("/logout/")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_practice_and_tests_share_application_navigation(self):
        self.client.force_login(self.student)
        for path in ("/learn/", "/tests/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'aria-label="Main navigation"')
                self.assertContains(response, reverse("portal_home"))
