from django.shortcuts import redirect
from .services import workspace_ready


class WorkspaceActivationMiddleware:
    """Require enrollment for learning and management routes, independently of sign-in."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        prefixes = ("/learn/", "/tests/", "/tutor/", "/study/", "/teach/", "/manage/", "/imports/", "/practice/", "/progress/", "/library/")
        tenant = getattr(request, "tenant", None)
        user = request.user
        direct_route = request.path.startswith(("/practice/", "/progress/", "/library/"))
        if tenant and not tenant.is_master and direct_route:
            return self.get_response(request)
        if user.is_authenticated and not user.email_verified:
            protected = request.path.startswith(prefixes) or request.path in ("/", "/activate/", "/workspace/open/")
            protected |= request.path.startswith("/admin/") and request.path not in ("/admin/login/", "/admin/logout/")
            if protected:
                return redirect("verify_email")
        if user.is_authenticated and request.path.startswith(prefixes):
            # Leave wrong-tenant sessions to the endpoint's tenant authorization.
            if tenant and not tenant.is_master and (user.tenant_id in (None, tenant.pk) or (user.tenant_id and user.tenant.is_master)) and not workspace_ready(user, tenant):
                return redirect("activate_account")
            if tenant is None and getattr(request, "is_core_domain", False):
                if not request.path.startswith(("/practice/", "/progress/", "/library/")):
                    return redirect("portal_home")
        return self.get_response(request)
