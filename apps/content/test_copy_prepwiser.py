import io
import tempfile

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.catalog.models import Chapter, Exam, Subject, Topic
from apps.content.models import Question
from apps.tenancy.models import Tenant


class CopyPrepwiserTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.source = Tenant.objects.create(slug="copy-source", name="Copy source")
        self.target = Tenant.objects.create(slug="copy-target", name="Copy target")
        self.exam = Exam.objects.create(slug="copy-exam", name="Copy exam")
        subject = Subject.objects.create(slug="copy-physics", name="Copy physics")
        topic = Topic.objects.create(chapter=Chapter.objects.create(subject=subject, name="Motion"), name="Speed")
        figure = default_storage.save("questions/source/diagram.png", ContentFile(b"example figure"))
        self.question = Question.objects.create(tenant=self.source, kind="PRACTICE", exam=self.exam,
            source_year=2026, topic=topic, source="https://prepwiser.in/questions/example",
            license="Fixture", stem="A question", answer="A", status="published", figure_paths=[figure])

    def copy(self, *flags):
        call_command("copy_prepwiser_questions", "--source-tenant=copy-source", "--target-tenant=copy-target",
                     *flags, stdout=io.StringIO())

    def test_copies_questions_and_independent_figures_once(self):
        self.copy()
        copied = Question.objects.get(tenant=self.target, source=self.question.source)
        self.assertEqual((copied.exam_id, copied.source_year, copied.answer), (self.exam.pk, 2026, "A"))
        self.assertNotEqual(copied.figure_paths, self.question.figure_paths)
        with default_storage.open(copied.figure_paths[0], "rb") as image:
            self.assertEqual(image.read(), b"example figure")
        self.copy()
        self.assertEqual(Question.objects.filter(tenant=self.target).count(), 1)

    def test_conflicting_answers_stop_the_copy(self):
        Question.objects.create(tenant=self.target, source=self.question.source, license="Fixture", stem="Existing",
                                answer="B", question_type=self.question.question_type)
        with self.assertRaises(CommandError):
            self.copy()
        self.assertEqual(Question.objects.filter(tenant=self.target).count(), 1)
