from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("scans", "0001_initial"), ("tenancy", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="scanimport", name="next_page",
            field=models.PositiveIntegerField(default=0, help_text="Number of pages successfully processed."),
        ),
        migrations.CreateModel(
            name="TenantOCRSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("backend", models.CharField(
                    max_length=24, default="vision",
                    choices=[("vision", "Vision model"),
                             ("docling_vision", "Docling layout and OCR + vision model")],
                )),
                ("page_batch_size", models.PositiveSmallIntegerField(default=10)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                               related_name="ocr_settings", to="tenancy.tenant")),
            ],
        ),
    ]
