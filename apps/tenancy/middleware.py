from django.conf import settings
from apps.tenancy.models import Tenant


class TenantResolutionMiddleware:
    """Resolve <tenant>.localhost or <tenant>.<PYQ_TENANT_BASE_DOMAIN>.

    Unknown tenant hosts stay unscoped. Tenant-facing views must then fail closed.
    A later hosted deployment should use an explicit TenantDomain table.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":", 1)[0].lower().rstrip(".")
        base = getattr(settings, "PYQ_TENANT_BASE_DOMAIN", "localhost")
        tenant = None
        if host.endswith("." + base):
            slug = host[: -(len(base) + 1)]
            if "." not in slug:
                tenant = Tenant.objects.filter(slug=slug, is_master=False, status=Tenant.Status.ACTIVE).first()
        from urllib.parse import urlsplit
        request.is_core_domain = host == urlsplit(settings.PYQ_CORE_ORIGIN).hostname
        if request.is_core_domain:
            tenant = Tenant.objects.filter(is_master=True).first()
        request.tenant = tenant
        return self.get_response(request)
