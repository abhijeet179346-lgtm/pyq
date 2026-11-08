import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import LoginHandoff, User
from apps.audit.models import AuditEvent
from apps.learning.models import AccessCode, BatchEnrollment, TeacherBatchAssignment
from apps.learning.services import code_digest, models_q_expiry, _is_rate_limited, _record_failed_join
from apps.tenancy.features import is_enabled


def workspace_ready(user, tenant):
    if not user.is_authenticated or not user.email_verified or tenant is None or tenant.status != "active":
        return False
    if user.is_superuser:
        return True
    if user.tenant_id != tenant.pk:
        return False
    if tenant.is_master:
        return True
    if user.role == "tenant_admin":
        return True
    if user.role == "teacher":
        return TeacherBatchAssignment.objects.filter(tenant=tenant, teacher=user, batch__active=True).exists()
    return BatchEnrollment.objects.filter(tenant=tenant, student=user, active=True, batch__active=True).exists()


def tenant_origin(tenant):
    if tenant.is_master:
        return settings.PYQ_CORE_ORIGIN
    origin = urlsplit(settings.PYQ_CORE_ORIGIN)
    host = f"{tenant.slug}.{settings.PYQ_TENANT_BASE_DOMAIN}"
    if origin.port:
        host += f":{origin.port}"
    return urlunsplit((origin.scheme, host, "", "", ""))


def create_handoff(user, tenant=None):
    target = tenant or user.tenant
    if target is None or target.status != "active" or not user.is_active or not user.email_verified:
        raise ValueError("Your institute is not available right now.")
    if not user.is_superuser and user.tenant_id != target.pk:
        raise ValueError("This account cannot access that institute.")
    raw = secrets.token_urlsafe(32)
    LoginHandoff.objects.create(token_digest=hashlib.sha256(raw.encode()).hexdigest(),
        user=user, tenant=target, expires_at=timezone.now() + timedelta(seconds=60))
    return tenant_origin(target) + "/auth/continue/?" + urlencode({"token": raw})


@transaction.atomic
def consume_handoff(raw, tenant):
    digest = hashlib.sha256(raw.encode()).hexdigest()
    verified_user = Q(user__email_verified_at__isnull=False)
    if not settings.REQUIRE_EMAIL_VERIFICATION:
        verified_user |= Q(user__is_active=True)
    grant = LoginHandoff.objects.filter(token_digest=digest, tenant=tenant,
        expires_at__gt=timezone.now(), consumed_at__isnull=True,
        user__is_active=True).filter(verified_user).filter(
            Q(user__tenant=tenant) | Q(user__is_superuser=True, user__tenant__isnull=True)
        ).select_related("user").first()
    if grant is None:
        return None
    updated = LoginHandoff.objects.filter(pk=digest, consumed_at__isnull=True,
        expires_at__gt=timezone.now()).update(consumed_at=timezone.now())
    return grant.user if updated else None


@transaction.atomic
def activate_account(*, user, code, tenant=None, ip_address="unknown"):
    """Enroll an authenticated account. Code consumption and seat allocation commit together."""
    if not user.email_verified:
        return None, "Verify your email before activating an access code."
    scope = tenant.pk if tenant else "core"
    if _is_rate_limited(scope, ip_address):
        return None, "Too many attempts. Try again in an hour."
    access = AccessCode.objects.filter(digest=code_digest(code), revoked=False,
        batch__active=True, tenant__status="active", used_count__lt=F("max_uses"))
    access = access.filter(models_q_expiry(timezone.now())).select_related("tenant", "batch").first()
    if access is None or (tenant and access.tenant_id != tenant.pk):
        _record_failed_join(scope, ip_address)
        return None, "That code is invalid, expired, or unavailable for this institute."
    target = access.tenant
    from_master = bool(user.tenant_id and user.tenant.is_master and not target.is_master)
    if not is_enabled(target, "access_code_enrollment"):
        return None, "Access-code enrollment is currently disabled for this institute."
    subscription = getattr(target, "subscription", None)
    if subscription and subscription.status not in ("trial", "active"):
        return None, "This institute is not accepting new enrollments right now."
    if user.tenant_id and user.tenant_id != target.pk and not from_master:
        return None, "This account belongs to a different institute. Sign in on that institute's domain."
    if user.is_superuser or user.role == "tenant_admin":
        return None, "This account already has administrator access."
    if (user.tenant_id is None or from_master) and User.objects.filter(tenant=target, email__iexact=user.email).exclude(pk=user.pk).exists():
        return None, "You already have an account at this institute. Sign in on its subdomain to activate it."
    if user.tenant_id and not from_master and user.role != access.role and (workspace_ready(user, target) or user.role != "student"):
        return None, "Use an access code matching your account role."
    if access.role == "student":
        membership = BatchEnrollment.objects.filter(tenant=target, batch=access.batch, student=user, active=True)
    else:
        membership = TeacherBatchAssignment.objects.filter(tenant=target, batch=access.batch, teacher=user)
    if membership.exists():
        return user, None
    if access.role == "student" and subscription:
        seats = User.objects.filter(tenant=target, role="student", is_active=True,
            batch_enrollments__active=True, batch_enrollments__batch__active=True).distinct().count()
        already_active = workspace_ready(user, target)
        if not already_active and seats >= subscription.active_student_limit:
            return None, "This institute has reached its student limit."
    consumed = AccessCode.objects.filter(pk=access.pk, revoked=False, used_count__lt=F("max_uses"))
    if consumed.filter(models_q_expiry(timezone.now())).update(used_count=F("used_count") + 1) != 1:
        return None, "That code has already been used."
    if from_master:
        import uuid
        user = User(username="institute-" + uuid.uuid4().hex, email=user.email,
            first_name=user.first_name, last_name=user.last_name, password=user.password,
            google_subject=user.google_subject, email_verified_at=user.email_verified_at,
            core_account=user, tenant=target)
    user.tenant = target
    user.role = access.role
    user.is_staff = access.role == "teacher"
    if user.pk:
        user.save(update_fields=["tenant", "role", "is_staff"])
    else:
        user.save()
    if access.role == "student":
        BatchEnrollment.objects.update_or_create(tenant=target, batch=access.batch, student=user,
            defaults={"active": True})
    else:
        TeacherBatchAssignment.objects.get_or_create(tenant=target, batch=access.batch, teacher=user)
    AuditEvent.objects.create(tenant=target, actor=user, action="access_code_activated",
        object_type="AccessCode", object_id=str(access.pk), after={"batch_id": access.batch_id, "role": user.role})
    return user, None
