import time
from datetime import timedelta
from unittest.mock import patch, Mock
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User, LoginHandoff
from apps.features.models import FeatureEntitlement, TenantSubscription
from apps.learning.models import AccessCode, Batch, BatchEnrollment
from apps.learning.services import code_digest
from apps.tenancy.models import Tenant
from apps.portal.services import create_handoff


class OnboardingFixtures:
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Demo School", slug="school")
        cls.other = Tenant.objects.create(name="Other School", slug="other")
        cls.batch = Batch.objects.create(tenant=cls.tenant, name="Physics batch")
        cls.password = "A-strong-onboarding-password-27"
        cls.pending = User.objects.create_user(username="pending", email="pending@example.test",
            password=cls.password, role="student", email_verified_at=timezone.now())
        cls.code = AccessCode.objects.create(tenant=cls.tenant, batch=cls.batch,
            digest=code_digest("ABCDEFGH"), role="student", max_uses=3)
        for key in ("access_code_enrollment", "batch_management", "google_login", "practice", "tests"):
            FeatureEntitlement.objects.create(tenant=cls.tenant, key=key, entitled=True)

    def setUp(self):
        cache.clear()
        self.core = Client(HTTP_HOST="localhost")
        self.school = Client(HTTP_HOST="school.localhost")


class OnboardingTests(OnboardingFixtures, TestCase):
    def test_core_and_tenant_show_login_and_google_entry(self):
        for client in (self.core, self.school):
            with self.subTest(client=client):
                response = client.get("/")
                self.assertContains(response, 'action="/login/"')
                self.assertContains(response, "Continue with Google")
                self.assertNotContains(response, 'aria-label="Main navigation"')

    def test_new_email_creates_pending_account_with_only_email_and_password(self):
        for client, tenant, email in ((self.core, None, "new@core.test"), (self.school, self.tenant, "new@school.test")):
            with self.subTest(email=email):
                response = client.post("/login/", {"email": email,
                    "password": self.password}, follow=True)
                self.assertEqual(response.request["PATH_INFO"], "/verify-email/")
                user = User.objects.get(email=email)
                self.assertEqual(user.tenant_id, tenant.pk if tenant else Tenant.objects.get(is_master=True).pk)
                self.assertFalse(user.email_verified)
                self.assertFalse(user.is_staff)
                self.assertEqual(user.first_name, "")
                self.assertTrue(user.check_password(self.password))
                self.assertFalse(BatchEnrollment.objects.filter(student=user).exists())

    def test_existing_email_signs_in_without_creating_another_user(self):
        before = User.objects.count()
        self.core.post("/login/", {"email": self.pending.email, "password": self.password})
        self.assertEqual(User.objects.count(), before)
        self.assertEqual(int(self.core.session["_auth_user_id"]), self.pending.pk)

    def test_wrong_password_never_creates_or_changes_existing_account(self):
        before = User.objects.count()
        response = self.core.post("/login/", {"email": self.pending.email, "password": "Wrong-password-123"})
        self.assertContains(response, "couldn&#x27;t sign you in")
        self.assertEqual(User.objects.count(), before)
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.check_password(self.password))
        self.assertNotIn("_auth_user_id", self.core.session)

    def test_google_only_account_cannot_gain_password_from_failed_login(self):
        self.pending.set_unusable_password()
        self.pending.google_subject = "google-only-user"
        self.pending.save()
        before = User.objects.count()
        self.core.post("/login/", {"email": self.pending.email, "password": self.password})
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.has_usable_password())
        self.assertEqual(User.objects.count(), before)
        self.assertNotIn("_auth_user_id", self.core.session)

    def test_new_account_validates_password_before_creating_user(self):
        response = self.core.post("/login/", {"email": "weak@example.test", "password": "123"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="weak@example.test").exists())

    def test_combined_login_normalizes_email(self):
        self.core.post("/login/", {"email": " New-Person@Example.Test ", "password": self.password})
        self.assertTrue(User.objects.filter(email="new-person@example.test").exists())

    def test_legacy_signup_redirects_and_has_no_separate_registration(self):
        self.assertRedirects(self.core.get("/signup/"), "/login/", fetch_redirect_response=False)
        response = self.core.get("/")
        self.assertNotContains(response, 'href="/signup/"')
        self.assertNotContains(response, 'name="first_name"')
        self.assertNotContains(response, 'name="password2"')

    def test_pending_login_lands_at_access_code_step(self):
        response = self.school.post("/login/", {"email": self.pending.email,
            "password": self.password}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/activate/")
        self.assertContains(response, "Enter your access code")

    def test_pending_users_cannot_bypass_activation(self):
        self.school.force_login(self.pending)
        for path in ("/", "/learn/", "/tests/", "/tutor/", "/study/topics/1/", "/manage/scans/", "/imports/questions/csv/"):
            with self.subTest(path=path):
                self.assertRedirects(self.school.get(path), "/activate/", fetch_redirect_response=False)

    def test_tenant_activation_creates_membership_and_consumes_code(self):
        self.school.force_login(self.pending)
        self.assertRedirects(self.school.post("/activate/", {"code": "abcdefgh"}), "/", fetch_redirect_response=False)
        self.pending.refresh_from_db()
        self.code.refresh_from_db()
        self.assertTrue(self.pending.tenant.is_master)
        institute_user = User.objects.get(core_account=self.pending, tenant=self.tenant)
        self.assertEqual(self.code.used_count, 1)
        self.assertTrue(BatchEnrollment.objects.filter(student=institute_user, batch=self.batch, active=True).exists())
        self.assertEqual(self.school.get("/").status_code, 200)

    def test_core_domain_does_not_require_or_consume_institute_codes(self):
        self.core.force_login(self.pending)
        response = self.core.post("/activate/", {"code": "ABCDEFGH"}, follow=True)
        self.assertContains(response, "Your personal learning workspace")
        self.pending.refresh_from_db()
        self.code.refresh_from_db()
        self.assertTrue(self.pending.tenant.is_master)
        self.assertEqual(self.code.used_count, 0)

    def test_invalid_code_does_not_activate(self):
        self.school.force_login(self.pending)
        self.assertContains(self.school.post("/activate/", {"code": "XXXXXXXX"}), "invalid")
        self.pending.refresh_from_db()
        self.code.refresh_from_db()
        self.assertTrue(self.pending.tenant.is_master)
        self.assertEqual(self.code.used_count, 0)

    def test_wrong_tenant_code_does_not_activate(self):
        client = Client(HTTP_HOST="other.localhost")
        client.force_login(self.pending)
        self.assertContains(client.post("/activate/", {"code": "ABCDEFGH"}), "invalid")
        self.code.refresh_from_db()
        self.assertEqual(self.code.used_count, 0)

    def test_expired_revoked_and_exhausted_codes_are_rejected(self):
        self.school.force_login(self.pending)
        for changes in ({"expires_at": timezone.now() - timedelta(minutes=1)},
                        {"revoked": True}, {"used_count": 3}):
            with self.subTest(changes=changes):
                AccessCode.objects.filter(pk=self.code.pk).update(expires_at=None, revoked=False, used_count=0)
                AccessCode.objects.filter(pk=self.code.pk).update(**changes)
                self.assertContains(self.school.post("/activate/", {"code": "ABCDEFGH"}), "invalid")
                self.assertFalse(BatchEnrollment.objects.filter(student=self.pending).exists())

    def test_disabled_enrollment_and_seat_limit_are_enforced(self):
        self.school.force_login(self.pending)
        FeatureEntitlement.objects.filter(tenant=self.tenant, key="access_code_enrollment").update(enabled_override=False)
        self.assertContains(self.school.post("/activate/", {"code": "ABCDEFGH"}), "disabled")
        FeatureEntitlement.objects.filter(tenant=self.tenant, key="access_code_enrollment").update(enabled_override=True)
        TenantSubscription.objects.create(tenant=self.tenant, status="trial", active_student_limit=0)
        self.assertContains(self.school.post("/activate/", {"code": "ABCDEFGH"}), "student limit")
        self.code.refresh_from_db()
        self.assertEqual(self.code.used_count, 0)

    def test_activation_is_idempotent_for_existing_membership(self):
        self.school.force_login(self.pending)
        for _ in range(2):
            self.school.post("/activate/", {"code": "ABCDEFGH"})
        self.code.refresh_from_db()
        self.assertEqual(self.code.used_count, 1)
        self.assertEqual(BatchEnrollment.objects.filter(student__core_account=self.pending).count(), 1)

    def test_expired_or_wrong_tenant_handoff_is_rejected(self):
        self.pending.tenant = self.tenant
        self.pending.save()
        url = urlsplit(create_handoff(self.pending))
        path = url.path + "?" + url.query
        other = Client(HTTP_HOST="other.localhost")
        self.assertRedirects(other.get(path), "/login/", fetch_redirect_response=False)
        LoginHandoff.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertRedirects(self.school.get(path), "/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.school.session)

    def test_unknown_host_cannot_authenticate(self):
        client = Client(HTTP_HOST="unknown.localhost")
        self.assertEqual(client.get("/login/").status_code, 404)
        self.assertEqual(client.get("/signup/").status_code, 404)

    @override_settings(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="")
    def test_unconfigured_google_has_no_fake_signin(self):
        response = self.core.get("/auth/google/start/", follow=True)
        self.assertContains(response, "not configured")
        self.assertNotIn("_auth_user_id", self.core.session)


@override_settings(GOOGLE_CLIENT_ID="google-client", GOOGLE_CLIENT_SECRET="google-secret")
class GoogleLoginTests(OnboardingFixtures, TestCase):
    def begin(self, tenant=None):
        response = self.core.get("/auth/google/start/", {"tenant": tenant} if tenant else {})
        self.assertEqual(response.status_code, 302)
        params = parse_qs(urlsplit(response.url).query)
        self.assertEqual(params["redirect_uri"], ["http://localhost:8765/auth/google/callback/"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        return self.core.session["google_login"]

    @patch("apps.portal.oauth.id_token.verify_oauth2_token")
    @patch("apps.portal.oauth.requests.post")
    def test_google_verified_direct_account_opens_learning_workspace(self, token_post, verify):
        flow = self.begin()
        token_post.return_value = Mock(json=lambda: {"id_token": "signed-id-token"})
        verify.return_value = {"sub": "google-123", "email": "learner@gmail.com",
            "email_verified": True, "nonce": flow["nonce"], "given_name": "Learner"}
        response = self.core.get("/auth/google/callback/", {"state": flow["state"], "code": "authorization-code"}, follow=True)
        self.assertContains(response, "Your personal learning workspace")
        user = User.objects.get(google_subject="google-123")
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.tenant.is_master)
        self.assertFalse(BatchEnrollment.objects.filter(student=user).exists())
        verify.assert_called_once()

    @patch("apps.portal.oauth.id_token.verify_oauth2_token")
    @patch("apps.portal.oauth.requests.post")
    def test_google_tenant_callback_uses_handoff_before_activation(self, token_post, verify):
        flow = self.begin("school")
        token_post.return_value = Mock(json=lambda: {"id_token": "signed-id-token"})
        verify.return_value = {"sub": "google-456", "email": "teacher@gmail.com",
            "email_verified": True, "nonce": flow["nonce"]}
        response = self.core.get("/auth/google/callback/", {"state": flow["state"], "code": "code"})
        destination = urlsplit(response.url)
        self.assertEqual(destination.hostname, "school.localhost")
        response = self.school.get(destination.path + "?" + destination.query, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/activate/")

    @patch("apps.portal.oauth.requests.post")
    def test_wrong_state_and_expired_flow_never_exchange_tokens(self, token_post):
        flow = self.begin()
        self.core.get("/auth/google/callback/", {"state": "wrong", "code": "code"})
        token_post.assert_not_called()
        flow = self.begin()
        flow["started"] = time.time() - 601
        session = self.core.session
        session["google_login"] = flow
        session.save()
        self.core.get("/auth/google/callback/", {"state": flow["state"], "code": "code"})
        token_post.assert_not_called()

    @patch("apps.portal.oauth.id_token.verify_oauth2_token")
    @patch("apps.portal.oauth.requests.post")
    def test_unverified_email_and_wrong_nonce_are_rejected(self, token_post, verify):
        for changes in ({"email_verified": False}, {"nonce": "wrong"}):
            flow = self.begin()
            token_post.return_value = Mock(json=lambda: {"id_token": "signed-id-token"})
            verify.return_value = {"sub": "untrusted", "email": "untrusted@gmail.com",
                "email_verified": True, "nonce": flow["nonce"], **changes}
            self.core.get("/auth/google/callback/", {"state": flow["state"], "code": "code"})
            self.assertFalse(User.objects.filter(google_subject="untrusted").exists())
            self.assertNotIn("_auth_user_id", self.core.session)

    def test_google_toggle_is_optional_for_each_tenant(self):
        FeatureEntitlement.objects.filter(tenant=self.tenant, key="google_login").update(enabled_override=False)
        response = self.school.get("/auth/google/start/", follow=True)
        self.assertContains(response, "not enabled")
        self.assertEqual(self.core.get("/auth/google/start/?tenant=school").status_code, 404)
