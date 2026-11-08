from django.db import migrations


def refingerprint_and_restore(apps, schema_editor):
    """Include figure files in fingerprints, and restore figure questions 0006 retired by mistake.

    0006 compared text only, so different figure questions with identical wording ("[See figure]")
    were treated as duplicates. Any retired question with figures that no longer matches a live
    question in its paper/institute bank is published again.
    """
    from apps.content.fingerprint import question_fingerprint
    Question = apps.get_model("content", "Question")
    changed = []
    for q in Question.objects.exclude(figure_paths=[]).only("id", "stem", "options", "figure_paths", "fingerprint"):
        fingerprint = question_fingerprint(q.stem, q.options, q.figure_paths)
        if fingerprint != q.fingerprint:
            q.fingerprint = fingerprint
            changed.append(q)
    Question.objects.bulk_update(changed, ["fingerprint"], batch_size=500)
    restored = []
    for q in Question.objects.filter(status="retired").exclude(figure_paths=[]).exclude(fingerprint=""):
        scope = {"paper_id": q.paper_id} if q.paper_id else {"tenant_id": q.tenant_id}
        if not Question.objects.filter(fingerprint=q.fingerprint, **scope).exclude(status="retired").exists():
            restored.append(q.pk)
    if restored:
        Question.objects.filter(pk__in=restored).update(status="published")
        print(f"\n  Restored {len(restored)} figure question(s) retired by mistake: {sorted(restored)}")


class Migration(migrations.Migration):
    dependencies = [("content", "0007_question_unique_fingerprint")]
    operations = [migrations.RunPython(refingerprint_and_restore, migrations.RunPython.noop)]
