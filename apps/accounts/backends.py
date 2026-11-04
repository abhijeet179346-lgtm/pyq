from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.db.models import Q


class TenantAwareBackend(BaseBackend):
    """Authenticate institute accounts locally and pending accounts before activation."""
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password or request is None:
            return None
        User = get_user_model()
        identifier = username.strip()
        tenant = getattr(request, "tenant", None)
        lookup = Q(email__iexact=identifier) | Q(username=identifier)
        if tenant is not None:
            candidates = User.objects.filter(tenant=tenant).filter(lookup)
            if not candidates.exists():
                candidates = User.objects.filter(tenant__isnull=True, is_superuser=True).filter(lookup)
            if not candidates.exists():
                candidates = User.objects.filter(Q(tenant__is_master=True) | Q(tenant__isnull=True),
                    is_superuser=False, is_staff=False, role="student").filter(lookup)
        elif getattr(request, "is_core_domain", False):
            if request.path.startswith("/admin/"):
                candidates = User.objects.filter(is_superuser=True).filter(lookup)
            else:
                candidates = User.objects.filter(lookup)
                core = candidates.filter(tenant__isnull=True)
                if core.exists():
                    candidates = core
        else:
            return None
        # An email may exist at multiple institutes. Require that institute's host
        # rather than selecting an arbitrary tenant account.
        if candidates.count() != 1:
            User().set_password(password)
            return None
        user = candidates.first()
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    @staticmethod
    def user_can_authenticate(user):
        return getattr(user, "is_active", True)

    def get_user(self, user_id):
        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None
