from django.conf import settings
from apps.tenancy.features import FEATURES, is_enabled
from .oauth import google_configured
from .services import workspace_ready
from .brand import accent_style


def portal_context(request):
    tenant = getattr(request, "tenant", None)
    return {"portal_tenant": tenant,
            "portal_accent_style": accent_style(getattr(tenant, "primary_color", "")),
            "portal_brand": (tenant.display_name or tenant.name) if tenant else "PYQNet",
            "workspace_ready": workspace_ready(request.user, tenant),
            "direct_ready": workspace_ready(request.user, tenant) and (request.user.tenant_id == tenant.pk or (request.user.is_superuser and request.user.tenant_id is None)),
            "is_core_domain": getattr(request, "is_core_domain", False),
            "verification_required": settings.REQUIRE_EMAIL_VERIFICATION,
            "google_available": google_configured() and (tenant is None or is_enabled(tenant, "google_login")),
            "portal_flags": {key: is_enabled(tenant, key) for key in FEATURES}}
