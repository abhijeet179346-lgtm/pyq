import re
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User, EmailVerification
from apps.features.models import FeatureEntitlement
from apps.learning.models import Batch, AccessCode, BatchEnrollment
from apps.learning.services import code_digest
from apps.portal.oauth import _google_account
from apps.portal.services import activate_account
from apps.portal.verification import send_verification, VerificationDeliveryError
from apps.tenancy.models import Tenant


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REQUIRE_EMAIL_VERIFICATION=True)
class EmailVerificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Verify-regression-password-27"
        cls.tenant = Tenant.objects.create(name="Verified School", slug="verified")
        cls.user = User.objects.create_user(username="unverified", email="learner@example.test",
            password=cls.password, tenant=cls.tenant)
        cls.other = User.objects.create_user(username="other", email="other@example.test",
            password=cls.password, tenant=cls.tenant)
        cls.batch = Batch.objects.create(tenant=cls.tenant, name="Verified batch")
        cls.code = AccessCode.objects.create(tenant=cls.tenant, batch=cls.batch,
            digest=code_digest("ABCDEFGH"), max_uses=2)
        for key in ("access_code_enrollment", "batch_management", "practice"):
            FeatureEntitlement.objects.create(tenant=cls.tenant, key=key, entitled=True)

    def setUp(self):
        self.client = Client(HTTP_HOST="verified.localhost")
        self.client.force_login(self.user)

    def sent_path(self):
        send_verification(self.user)
        url = re.search(r"https?://\S+/verify-email/confirm/[^\s]+/", mail.outbox[-1].body).group()
        self.assertTrue(url.startswith("http://verified.localhost:8765/"))
        return "/verify-email/confirm/" + url.split("/verify-email/confirm/")[1]

    def test_password_login_sends_email_and_requires_verification(self):
        self.client.logout()
        response = self.client.post("/login/", {"email": self.user.email, "password": self.password}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/verify-email/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertFalse(User.objects.get(pk=self.user.pk).email_verified)

    def test_new_password_account_must_verify_before_enrollment(self):
        client = Client(HTTP_HOST="localhost")
        response = client.post("/login/", {"email": "new@example.test", "password": self.password}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/verify-email/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("http://localhost:8765/verify-email/confirm/", mail.outbox[0].body)

    def test_verification_gates_all_workspace_routes_and_admin(self):
        self.user.role = "tenant_admin"
        self.user.is_staff = True
        self.user.save()
        for path in ("/", "/activate/", "/admin/open-workspace/", "/learn/", "/tests/", "/tutor/", "/admin/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertRedirects(response, "/verify-email/", fetch_redirect_response=False)

    def test_activation_service_cannot_bypass_verification(self):
        tenant, error = activate_account(user=self.user, code="ABCDEFGH", tenant=self.tenant)
        self.assertIsNone(tenant)
        self.assertIn("Verify your email", error)
        self.code.refresh_from_db()
        self.assertEqual(self.code.used_count, 0)
        self.assertFalse(BatchEnrollment.objects.exists())

    def test_opening_link_does_not_verify_until_confirmation(self):
        path = self.sent_path()
        self.assertContains(self.client.get(path), "Verify email &amp; continue")
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)
        response = self.client.post(path, follow=True)
        self.assertEqual(response.request["PATH_INFO"], "/activate/")
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertEqual(self.client.post("/activate/", {"code": "ABCDEFGH"}).status_code, 302)
        self.assertTrue(BatchEnrollment.objects.filter(student=self.user).exists())

    def test_link_is_single_use_and_only_digest_is_stored(self):
        path = self.sent_path()
        raw = path.rstrip("/").split("/")[-1]
        self.assertNotEqual(EmailVerification.objects.get().pk, raw)
        self.client.post(path)
        response = self.client.post(path, follow=True)
        self.assertContains(response, "invalid, expired, or already used")

    def test_expired_link_does_not_verify(self):
        path = self.sent_path()
        EmailVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.client.post(path)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_link_cannot_verify_a_different_account(self):
        path = self.sent_path()
        self.client.force_login(self.other)
        self.client.post(path)
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertFalse(self.user.email_verified)
        self.assertFalse(self.other.email_verified)

    def test_link_requires_signin_and_does_not_auto_login(self):
        path = self.sent_path()
        client = Client(HTTP_HOST="verified.localhost")
        response = client.get(path)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/login/?next="))
        self.assertNotIn("_auth_user_id", client.session)

    def test_signin_from_email_link_preserves_link_without_replacing_it(self):
        path = self.sent_path()
        EmailVerification.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        client = Client(HTTP_HOST="verified.localhost")
        response = client.post("/login/", {"email": self.user.email,
            "password": self.password, "next": path}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], path)
        self.assertContains(response, "Verify email &amp; continue")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EmailVerification.objects.count(), 1)
        client.post(path)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_resending_is_post_only_and_rate_limited(self):
        self.assertEqual(self.client.get("/verify-email/resend/").status_code, 405)
        self.client.post("/verify-email/resend/")
        self.client.post("/verify-email/resend/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EmailVerification.objects.count(), 1)

    def test_resending_invalidates_previous_link(self):
        old = self.sent_path()
        EmailVerification.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        self.client.post("/verify-email/resend/")
        self.assertEqual(len(mail.outbox), 2)
        self.client.post(old)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_email_change_requires_fresh_verification(self):
        self.user.email_verified_at = timezone.now()
        self.user.save()
        self.user.email = "changed@example.test"
        self.user.save(update_fields=["email"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_password_change_invalidates_old_verification_link(self):
        path = self.sent_path()
        self.user.set_password("Changed-verification-password-27")
        self.user.save()
        self.client.force_login(self.user)
        self.client.post(path)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_delivery_failure_rolls_back_token_and_allows_retry(self):
        with patch("apps.portal.verification.send_mail", side_effect=OSError("mail backend down")):
            with self.assertRaises(VerificationDeliveryError):
                send_verification(self.user)
        self.assertEqual(EmailVerification.objects.count(), 0)
        send_verification(self.user)
        self.assertEqual(len(mail.outbox), 1)

    def test_verified_google_email_is_accepted_and_unverified_password_removed(self):
        user = User.objects.create_user(username="gmail", email="owner@gmail.com", password=self.password)
        original_password = user.password
        verified = _google_account({"sub": "google-verified-subject", "email": user.email,
            "email_verified": True}, tenant=None)
        self.assertTrue(verified.email_verified)
        self.assertNotEqual(verified.password, original_password)
        self.assertFalse(verified.has_usable_password())

    def test_confirmation_requires_csrf(self):
        path = self.sent_path()
        client = Client(enforce_csrf_checks=True, HTTP_HOST="verified.localhost")
        client.force_login(self.user)
        self.assertEqual(client.post(path).status_code, 403)
