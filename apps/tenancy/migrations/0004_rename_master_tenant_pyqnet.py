from django.db import migrations

OLD_NAME, NEW_NAME = "PYQ Vault — Master tenant", "PYQNet — Master tenant"
OLD_LICENSE = "Original PYQ Vault revision notes; educational use."
NEW_LICENSE = "Original PYQNet revision notes; educational use."


def rename(apps, schema_editor, forward=True):
    Tenant = apps.get_model("tenancy", "Tenant")
    MaterialDocument = apps.get_model("materials", "MaterialDocument")
    pairs = [(OLD_NAME, NEW_NAME, "PYQ Vault", "PYQNet", OLD_LICENSE, NEW_LICENSE)]
    for old_name, new_name, old_display, new_display, old_lic, new_lic in pairs:
        if not forward:
            old_name, new_name, old_display, new_display, old_lic, new_lic = new_name, old_name, new_display, old_display, new_lic, old_lic
        # Only rename values still at the default; leave admin-customised names alone.
        Tenant.objects.filter(is_master=True, name=old_name).update(name=new_name)
        Tenant.objects.filter(is_master=True, display_name=old_display).update(display_name=new_display)
        MaterialDocument.objects.filter(license=old_lic).update(license=new_lic)


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0003_initialize_master_tenant"), ("materials", "0003_questionmateriallink")]
    operations = [migrations.RunPython(rename, lambda a, s: rename(a, s, forward=False))]
