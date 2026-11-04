from apps.tenancy.features import FEATURES, is_enabled
from .oauth import google_configured
from .services import workspace_ready


def portal_context(request):
    tenant = getattr(request, "tenant", None)
    return {"portal_tenant": tenant,
            "portal_brand": (tenant.display_name or tenant.name) if tenant else "PYQ Vault",
            "workspace_ready": workspace_ready(request.user, tenant),
            "direct_ready": request.user.is_authenticated and not request.user.is_superuser and request.user.email_verified and getattr(request, "is_core_domain", False) and tenant is not None and tenant.is_master and tenant.status == "active" and request.user.tenant_id == tenant.pk,
            "is_core_domain": getattr(request, "is_core_domain", False),
            "google_available": google_configured() and (tenant is None or is_enabled(tenant, "google_login")),
            "portal_flags": {key: is_enabled(tenant, key) for key in FEATURES}}
