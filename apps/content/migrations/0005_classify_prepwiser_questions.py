import re

from django.db import migrations


def classify_prepwiser_questions(apps, schema_editor):
    Question = apps.get_model("content", "Question")
    Exam = apps.get_model("catalog", "Exam")
    db = schema_editor.connection.alias
    exam = Exam.objects.using(db).filter(slug="jee-main").first()
    if exam is None:
        return
    questions = Question.objects.using(db).filter(
        tenant__isnull=False, kind="PRACTICE", exam__isnull=True,
        source__startswith="https://prepwiser.in/questions/", section__startswith="JEE Main",
    )
    years = {}
    for pk, section in questions.values_list("pk", "section").iterator(chunk_size=1000):
        match = re.search(r"\b(?:19|20)\d{2}\b", section)
        years.setdefault(int(match.group()) if match else None, []).append(pk)
    for year, ids in years.items():
        for offset in range(0, len(ids), 500):
            Question.objects.using(db).filter(pk__in=ids[offset:offset + 500]).update(exam_id=exam.pk, source_year=year)


class Migration(migrations.Migration):
    dependencies = [("content", "0004_question_exam_question_source_year")]
    operations = [migrations.RunPython(classify_prepwiser_questions, migrations.RunPython.noop)]
