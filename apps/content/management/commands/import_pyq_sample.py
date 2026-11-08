import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.audit.models import AuditEvent
from apps.catalog.models import Chapter, Exam, Paper, Subject, Topic, Subtopic
from apps.content.models import ConceptTag, Question, QuestionConceptTag, QuestionRevision
from apps.content.math_text import repair_text
from apps.features.models import TenantExam
from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = "Import the source-attributed sample of actual JEE Main, Advanced and NEET questions."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=str(settings.BASE_DIR / "data" / "pyq_sample.json"))
        parser.add_argument("--tenant", help="Also enable these exams for the specified existing tenant slug.")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--refresh-taxonomy", action="store_true", help="Update existing sample chapter/topic/subtopic assignments only; preserve question text and answers.")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            payload = json.loads(Path(options["path"]).read_text())
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        tenant = None
        if options["tenant"]:
            tenant = Tenant.objects.filter(slug=options["tenant"]).first()
            if tenant is None:
                raise CommandError("Unknown tenant slug.")
        exam_names = {"jee-main": "JEE Main", "jee-advanced": "JEE Advanced", "neet": "NEET", "comedk": "COMEDK", "mht-cet": "MHT-CET", "cuet-ug": "CUET UG"}
        created = skipped = updated = 0
        counts = {}
        for row in payload["questions"]:
            if row["exam"] not in exam_names:
                raise CommandError("Unknown exam in sample file.")
            exam, _ = Exam.objects.get_or_create(slug=row["exam"], defaults={"name": exam_names[row["exam"]]})
            subject, _ = Subject.objects.get_or_create(slug=slugify(row["subject"]), defaults={"name": row["subject"]})
            if not exam.subjects.filter(pk=subject.pk).exists():
                raise CommandError(f'{subject.name} is not enabled for {exam.name}. Update the exam in admin.')
            chapter, _ = Chapter.objects.get_or_create(subject=subject, name=row["chapter"])
            topic, _ = Topic.objects.get_or_create(chapter=chapter, name=row["topic"])
            subtopic = None
            if row.get("subtopic"):
                subtopic, _ = Subtopic.objects.get_or_create(topic=topic, name=row["subtopic"])
            concept, _ = ConceptTag.objects.get_or_create(topic=topic, name=row["topic"])
            paper, _ = Paper.objects.get_or_create(exam=exam, year=row["year"], sitting=row["sitting"])
            section = row.get("section") or f"{row['subject']} · Q{row['question_number']}"
            source = row["source_url"]
            if row.get("source_page"):
                source += f"#page={row['source_page']}"
            question = Question.objects.filter(tenant__isnull=True, paper=paper, section=section, source=source).first()
            if question:
                if options["refresh_taxonomy"] and (question.topic_id != topic.pk or question.subtopic_id != (subtopic.pk if subtopic else None)):
                    QuestionRevision.objects.get_or_create(question=question, version=question.version, defaults={"snapshot": {
                        "topic_id": question.topic_id, "subtopic_id": question.subtopic_id,
                        "stem": question.stem, "answer": question.answer, "options": question.options,
                        "solution": question.solution, "version": question.version}})
                    question.topic = topic
                    question.subtopic = subtopic
                    question.version += 1
                    question.full_clean()
                    question.save(update_fields=["topic", "subtopic", "version", "updated_at"])
                    question.concept_links.exclude(concept_tag__topic=topic).delete()
                    QuestionConceptTag.objects.get_or_create(question=question, concept_tag=concept)
                    updated += 1
                else:
                    skipped += 1
            else:
                answer = json.dumps(row["answer"]) if isinstance(row["answer"], list) else row["answer"]
                answer_options = [{**item, 'text': repair_text(item.get('text', ''))} if isinstance(item, dict) else repair_text(item)
                           for item in row["options"]]
                question = Question(tenant=None, kind=Question.Kind.PYQ, paper=paper, section=section,
                    question_type=row["question_type"], stem=repair_text(row["stem"]), options=answer_options, answer=answer,
                    solution=repair_text(row["solution"]) + "\n\nAnswer reference: " + row["answer_source_url"],
                    topic=topic, subtopic=subtopic, source=source, source_page=row.get("source_page"),
                    license=row.get("license", "Short PYQ restatement; original copyright retained; local evaluation sample."),
                    difficulty=row.get("difficulty", 4 if row["exam"] == "jee-advanced" else 2),
                    difficulty_basis=row.get("difficulty_basis", "Editorial estimate for local practice; not student-calibrated."),
                    taxonomy_basis=row.get("taxonomy_basis", "Editorial classification of question content."),
                    tags=row.get("tags", [exam.name, subject.name, chapter.name, topic.name, subtopic.name if subtopic else "", str(row["year"])]),
                    status=Question.Status.PUBLISHED)
                question.tags = list(dict.fromkeys([tag for tag in question.tags if tag] + [question.difficulty_label, question.get_question_type_display()]))
                if question.duplicate():
                    skipped += 1
                    continue
                question.full_clean()
                question.save()
                QuestionConceptTag.objects.create(question=question, concept_tag=concept)
                created += 1
            counts[row["exam"]] = counts.get(row["exam"], 0) + 1
            if tenant:
                TenantExam.objects.get_or_create(tenant=tenant, exam=exam, defaults={"enabled": True})
        AuditEvent.objects.create(tenant=tenant, action="pyq_sample_imported", object_type="Question",
            object_id="sample", after={"created": created, "skipped": skipped, "taxonomy_updated": updated, "exams": counts})
        if options["dry_run"]:
            transaction.set_rollback(True)
        self.stdout.write(self.style.SUCCESS(f"{'Dry run: ' if options['dry_run'] else ''}{created} created, {updated} taxonomy updated, {skipped} already present. {counts}"))
