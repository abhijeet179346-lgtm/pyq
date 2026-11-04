from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scans", "0002_tenant_ocr_settings_and_scan_progress")]

    operations = [
        migrations.AlterField(
            model_name="tenantocrsettings",
            name="backend",
            field=models.CharField(max_length=24, default="vision", choices=[
                ("vision", "Vision model"),
                ("docling_local", "Docling local OCR and question parsing"),
                ("docling_vision", "Docling layout and OCR + vision model"),
            ]),
        ),
    ]
