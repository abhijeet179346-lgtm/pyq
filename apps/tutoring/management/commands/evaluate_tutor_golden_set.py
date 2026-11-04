import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.content.models import QuestionConceptTag
from apps.tutoring.models import TenantAISettings, TutorGoldenCase
from apps.tutoring.services import _call_model, retrieve_sections


class Command(BaseCommand):
    help = "Evaluate the tutor against teacher-labelled cases, then enforce Phase 2 quality gates."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, required=True)
        parser.add_argument("--re-evaluate", action="store_true")
        parser.add_argument("--recompute-only", action="store_true")

    def handle(self, *args, **options):
        tenant_id = options["tenant"]
        cases = TutorGoldenCase.objects.filter(tenant_id=tenant_id)
        if not cases.exists():
            raise CommandError("Add teacher-labelled golden cases in the Tutor golden cases admin first.")
        if not options["recompute_only"]:
            pending = cases if options["re_evaluate"] else cases.filter(evaluated_at__isnull=True)
            for case in pending.select_related("question__topic", "expected_concept"):
                if not case.question.topic_id:
                    continue
                tags = list(QuestionConceptTag.objects.filter(question=case.question).select_related("concept_tag"))
                if not tags:
                    continue
                allowed = [row.concept_tag for row in tags]
                sections = retrieve_sections(case.tenant, case.question.topic,
                    case.question.stem + " " + case.student_answer, [tag.pk for tag in allowed])
                snippets = [{"id": section.pk, "title": section.title, "source": section.document.title,
                    "text": section.body[:5000]} for section in sections]
                response = _call_model(case.tenant, None, "golden_set_evaluation",
                    "Diagnose the student's misconception using only supplied material and the allowed concept ids. Return JSON with concept_id, explanation, and citation_ids. Do not use outside knowledge.",
                    json.dumps({"question": case.question.stem, "student_answer": case.student_answer,
                        "correct_answer": case.question.answer, "solution": case.question.solution,
                        "allowed_concepts": [{"id": tag.pk, "name": tag.name} for tag in allowed],
                        "material": snippets}))
                if response:
                    predicted = next((tag for tag in allowed if str(tag.pk) == str(response.get("concept_id"))), None)
                    case.predicted_concept = predicted
                    case.generated_answer = str(response.get("explanation", ""))[:5000]
                    valid_citations = {str(section.pk): {"section_id": section.pk, "title": section.title,
                        "document": section.document.title, "page": section.page_reference} for section in sections}
                    case.citations = [valid_citations[value] for value in map(str, response.get("citation_ids", [])) if value in valid_citations]
                    case.evaluated_at = timezone.now()
                    case.save(update_fields=["predicted_concept", "generated_answer", "citations", "evaluated_at"])

        evaluated = cases.filter(evaluated_at__isnull=False)
        total = evaluated.count()
        correct_diagnoses = evaluated.filter(predicted_concept=F("expected_concept")).count()
        diagnosis_accuracy = 100 * correct_diagnoses / total if total else 0
        graded_answers = evaluated.filter(answer_approved__isnull=False)
        answer_accuracy = 100 * graded_answers.filter(answer_approved=True).count() / graded_answers.count() if graded_answers.exists() else 0
        graded_citations = evaluated.filter(citations_supported__isnull=False)
        citation_accuracy = 100 * graded_citations.filter(citations_supported=True).count() / graded_citations.count() if graded_citations.exists() else 0
        ai = TenantAISettings.objects.filter(tenant_id=tenant_id).first()
        if ai is None:
            raise CommandError("Create Tenant AI settings and set a monthly budget before evaluation.")
        ai.diagnosis_accuracy_percent = diagnosis_accuracy
        ai.answer_accuracy_percent = answer_accuracy
        ai.citation_support_percent = citation_accuracy
        ai.diagnosis_golden_set_size = total
        ai.answer_golden_set_size = min(graded_answers.count(), graded_citations.count())
        ai.quality_approved = (total >= 500 and diagnosis_accuracy >= 85 and ai.answer_golden_set_size >= 300
            and answer_accuracy >= 90 and citation_accuracy >= 90)
        ai.save(update_fields=["diagnosis_accuracy_percent", "answer_accuracy_percent", "citation_support_percent",
            "diagnosis_golden_set_size", "answer_golden_set_size", "quality_approved", "updated_at"])
        AuditEvent.objects.create(tenant=ai.tenant, actor=None, action="tutor_quality_gate_evaluated",
            object_type="TenantAISettings", object_id=str(ai.pk), after={"diagnosis_accuracy": diagnosis_accuracy,
            "answer_accuracy": answer_accuracy, "citation_support": citation_accuracy,
            "diagnosis_cases": total, "reviewed_cases": ai.answer_golden_set_size, "approved": ai.quality_approved})
        self.stdout.write(self.style.SUCCESS(
            f"Diagnosis {diagnosis_accuracy:.1f}% ({total}); answer {answer_accuracy:.1f}%; citation support {citation_accuracy:.1f}%; approved={ai.quality_approved}"))
