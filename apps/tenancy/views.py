from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import TenantBrandingForm
from .features import is_enabled
from apps.audit.models import AuditEvent


@login_required
@require_http_methods(["GET", "POST"])
def tenant_branding(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None or request.user.tenant_id != tenant.pk or request.user.role != "tenant_admin":
        raise PermissionDenied("Only this institute's tenant admin can edit branding.")
    if not is_enabled(tenant, "branding_settings"):
        raise PermissionDenied("Branding settings are disabled for this institute.")
    if request.method == "POST":
        form = TenantBrandingForm(request.POST, instance=tenant)
        if form.is_valid():
            before = {key: getattr(tenant, key) for key in ("display_name", "primary_color", "logo_url", "welcome_text")}
            updated = form.save(commit=False)
            updated.save(update_fields=["display_name", "primary_color", "logo_url", "welcome_text"])
            AuditEvent.objects.create(tenant=tenant, actor=request.user, action="tenant_branding_updated",
                object_type="Tenant", object_id=str(tenant.pk), before=before,
                after={key: getattr(updated, key) for key in before})
            messages.success(request, "Institute branding saved.")
            return redirect("tenant_branding")
    else:
        form = TenantBrandingForm(instance=tenant)
    return render(request, "tenancy/branding.html", {"form": form, "tenant": tenant})
