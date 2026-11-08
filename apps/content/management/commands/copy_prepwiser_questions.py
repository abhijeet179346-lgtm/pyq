"""Copy imported PrepWiser questions and private figures between tenants."""

from copy import deepcopy
from pathlib import PurePosixPath

from django.core.management.base import BaseCommand, CommandError
from django.core.files.storage import default_storage
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.content.figures import open_figure, store
from apps.content.models import Question
from apps.tenancy.models import Tenant


SOURCE_PREFIX = "https://prepwiser.in/questions/"


class Command(BaseCommand):
    help = "Copy tenant-owned PrepWiser questions and their figures to another tenant."

    def add_arguments(self, parser):
        parser.add_argument("--source-tenant", default="demo", help="Source tenant slug (default: demo).")
        parser.add_argument("--target-tenant", default="master", help="Target tenant slug (default: master).")
        parser.add_argument("--dry-run", action="store_true", help="Check the source and destination without writing.")

    def handle(self, *args, **options):
        source = Tenant.objects.filter(slug=options["source_tenant"]).first()
        target = Tenant.objects.filter(slug=options["target_tenant"]).first()
        if source is None or target is None:
            raise CommandError("Both source and target tenant slugs must exist.")
        if source.pk == target.pk:
            raise CommandError("Source and target tenants must be different.")

        source_questions = list(Question.objects.filter(tenant=source, source__startswith=SOURCE_PREFIX).order_by("id"))
        if not source_questions:
            raise CommandError(f"No PrepWiser questions were found in tenant '{source.slug}'.")
        source_links = [question.source for question in source_questions]
        if len(source_links) != len(set(source_links)):
            raise CommandError("The source tenant contains duplicate PrepWiser source links.")
        target_rows = list(Question.objects.filter(tenant=target, source__startswith=SOURCE_PREFIX)
                           .values("source", "answer", "question_type"))
        existing = {row["source"]: row for row in target_rows}
        if len(existing) != len(target_rows):
            raise CommandError("The target tenant contains duplicate PrepWiser source links.")
        conflicts = [question.source for question in source_questions if question.source in existing and
                     (question.answer != existing[question.source]["answer"] or
                      question.question_type != existing[question.source]["question_type"])]
        if conflicts:
            raise CommandError(f"{len(conflicts)} existing target questions have different answers or question types.")
        pending = [question for question in source_questions if question.source not in existing]
        paths = {path for question in pending for path in (question.figure_paths or [])}
        missing = [path for path in paths if not isinstance(path, str) or not path or ".." in path or
                   path.startswith("/") or not default_storage.exists(path)]
        if missing:
            raise CommandError(f"{len(missing)} source figure files are missing or invalid; nothing was copied.")
        self.stdout.write(f"{len(pending)} questions and {len(paths)} figure files to copy; "
                          f"{len(source_questions) - len(pending)} already present.")
        if options["dry_run"] or not pending:
            return

        copied_paths = {}
        try:
            for index, path in enumerate(sorted(paths), 1):
                with open_figure(path) as image:
                    extension = PurePosixPath(path).suffix.lstrip(".").lower()
                    if extension not in {"png", "jpg", "jpeg", "webp", "gif"}:
                        raise CommandError(f"Unsupported source figure format: {path}")
                    copied_paths[path] = store(image.read(), extension, f"tenant-{target.pk}/prepwiser")
                if index % 500 == 0 or index == len(paths):
                    self.stdout.write(f"Copied {index}/{len(paths)} figure files.")

            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=target.pk)
                overlap = Question.objects.filter(tenant=target, source__in=[q.source for q in pending]).exists()
                if overlap:
                    raise CommandError("Target questions changed during the copy; retry the command.")
                batch = []
                for question in pending:
                    batch.append(Question(
                        tenant=target, kind=question.kind, paper_id=question.paper_id,
                        exam_id=question.exam_id, source_year=question.source_year, section=question.section,
                        question_type=question.question_type, stem=question.stem,
                        options=deepcopy(question.options), answer=question.answer, solution=question.solution,
                        difficulty=question.difficulty, difficulty_basis=question.difficulty_basis,
                        taxonomy_basis=question.taxonomy_basis, tags=deepcopy(question.tags),
                        topic_id=question.topic_id, subtopic_id=question.subtopic_id, source=question.source,
                        license=question.license, status=question.status, ai_generated=question.ai_generated,
                        source_page=question.source_page,
                        figure_paths=[copied_paths[path] for path in (question.figure_paths or [])],
                    ))
                if batch:
                    from apps.content.models import without_duplicates
                    Question.objects.bulk_create(without_duplicates(batch), batch_size=500)
                AuditEvent.objects.create(tenant=target, action="prepwiser_questions_copied",
                    object_type="Question", object_id=source.slug,
                    after={"source_tenant": source.slug, "created": len(pending), "figure_files": len(copied_paths)})
        except Exception:
            for path in copied_paths.values():
                default_storage.delete(path)
            raise
        self.stdout.write(self.style.SUCCESS(f"Copied {len(pending)} questions into tenant '{target.slug}'."))
