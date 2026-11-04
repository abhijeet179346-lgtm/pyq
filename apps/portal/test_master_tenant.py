from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib import admin
from django.test import Client, TestCase, RequestFactory, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Exam, Paper, Subject, Chapter, Topic
from apps.content.models import Question
from apps.features.models import FeatureEntitlement, TenantExam
from apps.materials.models import MaterialDocument, MaterialSection
from apps.tenancy.models import Tenant


class MasterTenantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = Tenant.objects.get(is_master=True)
        cls.user = User.objects.create_user(username="core", email="core@example.test",
            password="Master-tenant-password-27", email_verified_at=timezone.now())
        cls.other = Tenant.objects.create(name="Independent institute", slug="independent")
        cls.institute_user = User.objects.create_user(username="institute", email="institute@example.test",
            password="Master-tenant-password-27", tenant=cls.other, email_verified_at=timezone.now())
        cls.exam = Exam.objects.create(slug="master-test-exam", name="Master test exam")
        TenantExam.objects.create(tenant=cls.master, exam=cls.exam, enabled=True)
        paper = Paper.objects.create(exam=cls.exam, year=2024)
        subject = Subject.objects.create(slug="master-physics", name="Master physics")
        chapter = Chapter.objects.create(subject=subject, name="Mechanics")
        cls.topic = Topic.objects.create(chapter=chapter, name="Forces")
        common = dict(topic=cls.topic, answer="A", options=["First", "Second"],
            source="Test fixture", license="Test only", status="published")
        cls.pyq = Question.objects.create(kind="PYQ", paper=paper, stem="Enabled exam PYQ", **common)
        cls.owned = Question.objects.create(tenant=cls.master, kind="PRACTICE", stem="Master-owned question", **common)
        cls.private = Question.objects.create(tenant=cls.other, kind="PRACTICE", stem="Institute-private question", **common)
        doc = MaterialDocument.objects.create(tenant=cls.master, title="Master material", source="Test", license="Test")
        MaterialSection.objects.create(document=doc, topic=cls.topic, title="Master lesson", body="Core-owned study material")

    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.client.force_login(self.user)

    def test_core_domain_and_new_core_users_belong_to_master(self):
        self.assertEqual(self.user.tenant_id, self.master.pk)
        self.assertEqual(self.client.get("/").wsgi_request.tenant.pk, self.master.pk)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertRedirects(self.client.get("/activate/"), "/", fetch_redirect_response=False)

    def test_core_registration_assigns_master_and_does_not_enroll_in_institute(self):
        client = Client(HTTP_HOST="localhost")
        client.post("/login/", {"email": "brand-new@example.test", "password": "Master-tenant-password-27"})
        user = User.objects.get(email="brand-new@example.test")
        self.assertEqual(user.tenant_id, self.master.pk)
        self.assertFalse(user.email_verified)
        self.assertFalse(user.batch_enrollments.exists())

    def test_exam_switch_controls_global_pyqs_without_hiding_master_owned_questions(self):
        response = self.client.get("/practice/")
        self.assertContains(response, self.exam.name)
        self.assertContains(response, "Additional practice")
        self.assertNotContains(response, self.pyq.stem)
        self.assertContains(self.client.get(f"/practice/?exam={self.exam.pk}&view=browse"), self.pyq.stem)
        self.assertContains(self.client.get("/practice/?exam=practice&view=browse"), self.owned.stem)
        self.assertNotContains(self.client.get("/practice/?exam=practice&view=browse"), self.private.stem)
        TenantExam.objects.filter(tenant=self.master, exam=self.exam).update(enabled=False)
        self.assertNotContains(self.client.get("/practice/"), self.exam.name)
        self.assertEqual(self.client.get(f"/practice/?exam={self.exam.pk}").status_code, 404)
        self.assertContains(self.client.get("/practice/?exam=practice&view=browse"), self.owned.stem)
        self.assertEqual(self.client.get(f"/practice/questions/{self.pyq.pk}/").status_code, 404)

    def test_feature_switches_control_navigation_and_direct_urls(self):
        for feature, path in (("practice", "/practice/"), ("study_material", "/library/"), ("mastery_analytics", "/progress/")):
            with self.subTest(feature=feature):
                FeatureEntitlement.objects.filter(tenant=self.master, key=feature).update(enabled_override=False)
                self.assertEqual(self.client.get(path).status_code, 403)
                self.assertNotContains(self.client.get("/"), f'href="{path}"')
                FeatureEntitlement.objects.filter(tenant=self.master, key=feature).update(enabled_override=True)

    def test_master_material_is_visible_only_to_core_context(self):
        self.assertContains(self.client.get("/library/"), "Core-owned study material")
        self.client.force_login(self.institute_user)
        self.assertEqual(self.client.get("/library/").status_code, 403)
        self.assertEqual(self.client.get("/").status_code, 403)

    def test_master_suspension_blocks_core_workspace(self):
        self.master.status = "suspended"
        self.master.save()
        self.assertEqual(self.client.get("/").status_code, 403)
        self.assertEqual(self.client.get("/practice/").status_code, 403)

    def test_master_cannot_be_deleted_through_admin(self):
        root = User.objects.create_superuser(username="root-master", email="root-master@example.test", password=None)
        request = RequestFactory().get("/admin/")
        request.user = root
        model_admin = admin.site._registry[Tenant]
        self.assertFalse(model_admin.has_delete_permission(request, self.master))
        model_admin.delete_queryset(request, Tenant.objects.filter(pk=self.master.pk))
        self.assertTrue(Tenant.objects.filter(pk=self.master.pk).exists())

    @override_settings(GOOGLE_CLIENT_ID="test-client", GOOGLE_CLIENT_SECRET="test-secret")
    @patch("apps.portal.oauth.id_token.verify_oauth2_token")
    @patch("apps.portal.oauth.requests.post")
    def test_google_core_flow_uses_master_without_subdomain_handoff(self, post, verify):
        client = Client(HTTP_HOST="localhost")
        start = client.get("/auth/google/start/")
        self.assertEqual(urlsplit(start.url).hostname, "accounts.google.com")
        flow = client.session["google_login"]
        self.assertEqual(flow["tenant"], self.master.pk)
        post.return_value = Mock(json=lambda: {"id_token": "mocked"})
        verify.return_value = {"sub": "master-google", "email": "master@gmail.com", "email_verified": True, "nonce": flow["nonce"]}
        response = client.get("/auth/google/callback/", {"state": flow["state"], "code": "mocked-code"}, follow=True)
        self.assertContains(response, "Your personal learning workspace")
        self.assertEqual(User.objects.get(google_subject="master-google").tenant_id, self.master.pk)
