from getpass import getpass
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog.models import Exam, Subject
from apps.tenancy.features import FEATURES
from apps.features.models import FeatureEntitlement, TenantExam, TenantSubscription
from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Create an initial PYQNet tenant, platform admin, tenant admin, and Phase 0 catalog seeds."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-name", required=True)
        parser.add_argument("--tenant-slug")
        parser.add_argument("--admin-email", required=True)
        parser.add_argument("--admin-username")

    def handle(self, *args, **opts):
        tenant_name = opts["tenant_name"].strip()
        slug = slugify(opts["tenant_slug"] or tenant_name)
        email = opts["admin_email"].strip().lower()
        username = opts["admin_username"] or f"{slugify(email)[:80]}-{slug[:20]}"
        if Tenant.objects.filter(slug=slug).exists():
            raise CommandError(f"Tenant slug '{slug}' already exists.")
        if get_user_model().objects.filter(username=username).exists():
            raise CommandError(f"Username '{username}' already exists.")
        password = getpass("Tenant admin password: ")
        confirm = getpass("Confirm password: ")
        if len(password) < 12 or password != confirm:
            raise CommandError("Passwords must match and contain at least 12 characters.")

        tenant = Tenant.objects.create(name=tenant_name, slug=slug)
        TenantSubscription.objects.create(tenant=tenant, plan=TenantSubscription.Plan.PILOT,
            status=TenantSubscription.Status.TRIAL, active_student_limit=100,
            trial_ends_at=timezone.now() + timedelta(days=60))
        User = get_user_model()
        admin = User.objects.create_user(username=username, email=email, password=password,
                                         tenant=tenant, role=User.Role.TENANT_ADMIN, is_staff=True)
        # Entitlements are separate from the tenant's on/off override.
        enabled = {"google_login", "global_pyq_bank", "tenant_question_bank", "bulk_question_import", "practice",
                   "tests", "custom_test_assignment", "batch_analytics", "access_code_enrollment",
                   "batch_management", "teacher_assignments", "branding_settings", "import_reports"}
        for key in FEATURES:
            FeatureEntitlement.objects.create(tenant=tenant, key=key, entitled=key in enabled,
                                              enabled_override=True if key in enabled else None)
        exam, _ = Exam.objects.get_or_create(slug="jee-main", defaults={"name": "JEE Main"})
        TenantExam.objects.get_or_create(tenant=tenant, exam=exam, defaults={"enabled": True})
        Subject.objects.get_or_create(slug="physics", defaults={"name": "Physics"})
        self.stdout.write(self.style.SUCCESS(
            f"Created tenant '{tenant.name}' ({tenant.slug}) and admin '{admin.username}'. "
            "Sign in at /admin/ on the tenant host."
        ))
