import csv
import hashlib
from django.utils import timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.tenancy.features import is_enabled
from apps.audit.models import AuditEvent
from apps.learning.models import TeacherSubjectAssignment
from .models import ImportBatch
from .forms import QuestionCSVForm
from .services import commit_questions, parse_and_validate_csv, to_preview_rows


def _authorize(request):
    tenant = getattr(request, "tenant", None)
    if not request.user.is_authenticated or tenant is None or request.user.tenant_id != tenant.id:
        raise PermissionDenied("A signed-in user on the matching tenant host is required.")
    if request.user.role not in ("tenant_admin", "teacher"):
        raise PermissionDenied("Only tenant admins and teachers may import questions.")
    for feature in ("tenant_question_bank", "bulk_question_import"):
        if not is_enabled(tenant, feature):
            raise PermissionDenied(f"The {feature} feature is not enabled for this institute.")
    return tenant


@login_required
@require_http_methods(["GET", "POST"])
def question_csv_import(request):
    tenant = _authorize(request)
    if request.method == "GET":
        return render(request, "imports/question_csv.html", {"form": QuestionCSVForm()})
    form = QuestionCSVForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "imports/question_csv.html", {"form": form}, status=400)
    uploaded = form.cleaned_data["file"]
    allowed_subject_ids = None
    if request.user.role == "teacher":
        allowed_subject_ids = set(TeacherSubjectAssignment.objects.filter(tenant=tenant, teacher=request.user)
                                  .values_list("subject_id", flat=True))
    rows, errors = parse_and_validate_csv(uploaded.read(), tenant, allowed_subject_ids)
    import_batch = ImportBatch.objects.create(tenant=tenant, initiated_by=request.user, file_name=uploaded.name,
        status=ImportBatch.Status.NEEDS_ATTENTION if errors else ImportBatch.Status.READY,
        row_count=len(rows), valid_count=sum(1 for row in rows if not row.get("_errors")), errors=errors)
    AuditEvent.objects.create(tenant=tenant, actor=request.user, action="question_csv_previewed",
        object_type="ImportBatch", object_id=str(import_batch.pk),
        after={"file_name": uploaded.name, "status": import_batch.status,
               "row_count": import_batch.row_count, "valid_count": import_batch.valid_count,
               "error_count": len(errors)})
    token = hashlib.sha256(f"{tenant.pk}:{request.user.pk}:{uploaded.name}:{uploaded.size}".encode()).hexdigest()[:32]
    request.session["question_import_preview"] = {
        "token": token, "tenant_id": tenant.pk, "user_id": request.user.pk, "import_batch_id": import_batch.pk,
        "rows": rows, "errors": errors,
    }
    request.session.modified = True
    return render(request, "imports/question_csv.html", {
        "form": QuestionCSVForm(), "preview": to_preview_rows(rows), "errors": errors,
        "token": token, "can_commit": bool(rows) and not errors, "import_batch": import_batch,
    })


@login_required
@require_http_methods(["POST"])
def commit_question_csv(request):
    tenant = _authorize(request)
    preview = request.session.get("question_import_preview", {})
    if (not preview or preview.get("token") != request.POST.get("token")
            or preview.get("tenant_id") != tenant.pk or preview.get("user_id") != request.user.pk):
        raise PermissionDenied("Import preview expired or does not belong to this account.")
    if preview.get("errors"):
        raise PermissionDenied("Fix CSV validation errors and upload it again before committing.")
    with transaction.atomic():
        created = commit_questions(preview["rows"], tenant, request.user)
        report = ImportBatch.objects.get(pk=preview["import_batch_id"], tenant=tenant, initiated_by=request.user)
        report.status = ImportBatch.Status.COMPLETED
        report.created_count = len(created)
        report.completed_at = timezone.now()
        report.save(update_fields=["status", "created_count", "completed_at"])
        AuditEvent.objects.create(tenant=tenant, actor=request.user, action="question_csv_imported",
                                  object_type="Question", object_id="bulk",
                                  after={"count": len(created), "ids": [str(obj.pk) for obj in created]})
    del request.session["question_import_preview"]
    messages.success(request, f"Imported {len(created)} questions as drafts.")
    return redirect("question_csv_import")


def question_csv_template(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="pyq-vault-question-template.csv"'
    writer = csv.writer(response)
    writer.writerow(["question_type", "stem", "options", "answer", "solution", "difficulty", "subject", "chapter", "topic", "source", "license"])
    writer.writerow(["single_correct", "What is $2+2$?", '["3", "4", "5", "6"]', "B", "Because two plus two is four.", 1, "Physics", "Mechanics", "Units", "Institute DPP 1", "Institute-owned"])
    return response
