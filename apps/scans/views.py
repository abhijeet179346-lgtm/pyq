import json
import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.audit.models import AuditEvent
from apps.learning.models import TeacherSubjectAssignment
from apps.catalog.models import Topic
from apps.content.models import Question
from apps.learning.views import _tenant_user
from apps.tenancy.features import is_enabled
from .forms import ScanUploadForm
from .models import ScanImport
from .services import approve_extracted


def _can_manage_scans(request):
    tenant = _tenant_user(request)
    if request.user.role not in ("tenant_admin", "teacher"):
        raise PermissionDenied("Only institute admins and assigned teachers may manage scans.")
    if not is_enabled(tenant, "scan_import"):
        raise PermissionDenied("Scanned imports are disabled for this institute.")
    return tenant


@login_required
@require_http_methods(["GET", "POST"])
def scan_import_list(request):
    tenant = _can_manage_scans(request)
    form = ScanUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        uploaded = form.cleaned_data["file"]
        scan = ScanImport.objects.create(tenant=tenant, initiated_by=request.user, file=uploaded,
            file_name=uploaded.name, source=form.cleaned_data["source"], license=form.cleaned_data["license"])
        AuditEvent.objects.create(tenant=tenant, actor=request.user, action="scan_import_uploaded",
            object_type="ScanImport", object_id=str(scan.pk), after={"file_name": scan.file_name})
        messages.success(request, "Scan uploaded. Run `python manage.py process_scan_imports --all-pages` in the worker to extract questions for review.")
        return redirect("scan_import_detail", scan_id=scan.pk)
    scans = ScanImport.objects.filter(tenant=tenant)
    if request.user.role == "teacher":
        scans = scans.filter(initiated_by=request.user)
    return render(request, "scans/list.html", {"form": form, "scans": scans[:100]})


@login_required
@require_http_methods(["GET", "POST"])
def scan_import_detail(request, scan_id):
    tenant = _can_manage_scans(request)
    scan = get_object_or_404(ScanImport, pk=scan_id, tenant=tenant)
    if request.user.role == "teacher" and scan.initiated_by_id != request.user.pk:
        raise PermissionDenied("This scan was uploaded by another teacher.")
    if request.method == "POST":
        try:
            selected = [value for value in request.POST.getlist("selected") if value.isdigit()]
            allowed = None
            if request.user.role == "teacher":
                allowed = set(TeacherSubjectAssignment.objects.filter(tenant=tenant, teacher=request.user)
                              .values_list("subject__chapters__topics__id", flat=True))
            edits = {}
            for index in selected:
                try:
                    options = json.loads(request.POST.get(f"options_{index}", "[]"))
                except json.JSONDecodeError:
                    raise ValueError(f"Row {int(index) + 1}: options must be a JSON array.")
                if not isinstance(options, list): raise ValueError(f"Row {int(index) + 1}: options must be a JSON array.")
                try:
                    tags = json.loads(request.POST.get(f"tags_{index}", "[]"))
                except json.JSONDecodeError:
                    raise ValueError(f"Row {int(index) + 1}: tags must be a JSON array.")
                if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                    raise ValueError(f"Row {int(index) + 1}: tags must be a list of text labels.")
                edits[index] = {"topic_id": request.POST.get(f"topic_{index}"),
                    "stem": request.POST.get(f"stem_{index}", ""), "answer": request.POST.get(f"answer_{index}", ""),
                    "solution": request.POST.get(f"solution_{index}", ""), "question_type": request.POST.get(f"type_{index}"),
                    "difficulty": request.POST.get(f"difficulty_{index}", 3), "options": options, "tags": tags}
            created = approve_extracted(scan, selected, edits, request.user, allowed_topic_ids=allowed)
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Created {len(created)} draft questions. Review and publish them from the question admin.")
        return redirect("scan_import_detail", scan_id=scan.pk)
    topics = Topic.objects.select_related("chapter__subject").order_by("chapter__subject__name", "chapter__name", "name")
    if request.user.role == "teacher":
        subjects = TeacherSubjectAssignment.objects.filter(tenant=tenant, teacher=request.user).values_list("subject_id", flat=True)
        topics = topics.filter(chapter__subject_id__in=subjects)
    rows = []
    for index, item in enumerate(scan.extraction_payload):
        rows.append({"index": index, **item, "options_json": json.dumps(item.get("options", []), ensure_ascii=False),
                     "tags_json": json.dumps(item.get("tags", []), ensure_ascii=False)})
    return render(request, "scans/detail.html", {"scan": scan, "rows": rows, "topics": topics,
        "question_types": Question.Type.choices})


@login_required
@require_http_methods(["GET"])
def scan_page_image(request, scan_id, page_number):
    tenant = _can_manage_scans(request)
    scan = get_object_or_404(ScanImport, pk=scan_id, tenant=tenant)
    if request.user.role == "teacher" and scan.initiated_by_id != request.user.pk:
        raise PermissionDenied("This scan was uploaded by another teacher.")
    if page_number < 1 or page_number > len(scan.page_image_paths):
        raise Http404("Page image not found.")
    path = scan.page_image_paths[page_number - 1]
    if not path or not default_storage.exists(path):
        raise Http404("Page image not found.")
    return FileResponse(default_storage.open(path, "rb"),
        content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
