from django.db import migrations


def initialize_master(apps, schema_editor):
    Tenant = apps.get_model("tenancy", "Tenant")
    User = apps.get_model("accounts", "User")
    Feature = apps.get_model("features", "FeatureEntitlement")
    TenantExam = apps.get_model("features", "TenantExam")
    Exam = apps.get_model("catalog", "Exam")
    slug = "master"
    counter = 1
    while Tenant.objects.filter(slug=slug, is_master=False).exists():
        slug = f"master-platform-{counter}"
        counter += 1
    master, _ = Tenant.objects.get_or_create(is_master=True, defaults={
        "name": "PYQ Vault — Master tenant", "display_name": "PYQ Vault", "slug": slug,
        "welcome_text": "Your personal learning workspace."})
    User.objects.filter(tenant__isnull=True, is_superuser=False, is_staff=False,
                        role="student").update(tenant=master)
    for key in ("practice", "global_pyq_bank", "tenant_question_bank", "study_material",
                "ncert_studio", "mastery_analytics", "google_login", "material_import",
                "bulk_question_import", "import_reports"):
        Feature.objects.get_or_create(tenant=master, key=key, defaults={"entitled": True})
    for exam in Exam.objects.filter(active=True):
        TenantExam.objects.get_or_create(tenant=master, exam=exam, defaults={"enabled": True})


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0002_tenant_is_master_tenant_single_master_tenant"),
        ("accounts", "0004_user_core_account"),
        ("features", "0001_initial"),
        ("catalog", "0001_initial"),
    ]
    operations = [migrations.RunPython(initialize_master, migrations.RunPython.noop)]
