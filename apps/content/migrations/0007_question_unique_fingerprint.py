from django.db import migrations, models


class Migration(migrations.Migration):
    # Separate from 0006 so the unique indexes are built after the data update has committed.
    dependencies = [("content", "0006_question_fingerprint")]

    operations = [
        migrations.AddConstraint(
            model_name='question',
            constraint=models.UniqueConstraint(condition=models.Q(('paper__isnull', False), models.Q(('fingerprint', ''), _negated=True), models.Q(('status', 'retired'), _negated=True)), fields=('paper', 'fingerprint'), name='uniq_question_per_paper'),
        ),
        migrations.AddConstraint(
            model_name='question',
            constraint=models.UniqueConstraint(condition=models.Q(('tenant__isnull', False), models.Q(('fingerprint', ''), _negated=True), models.Q(('status', 'retired'), _negated=True)), fields=('tenant', 'fingerprint'), name='uniq_question_per_tenant'),
        ),
    ]
