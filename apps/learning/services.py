import hashlib
import hmac
import secrets
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from apps.audit.models import AuditEvent
from .models import AccessCode, BatchEnrollment

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def code_digest(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.strip().upper().encode(), hashlib.sha256).hexdigest()


@transaction.atomic
def issue_codes(*, tenant, batch, creator, count=1, shared=False, max_uses=10000,
                role=AccessCode.Role.STUDENT, expires_at=None):
    if batch.tenant_id != tenant.pk:
        raise ValueError("Batch belongs to a different tenant.")
    if shared:
        count = 1
        max_uses = int(max_uses)
    else:
        max_uses = 1
    if max_uses < 1 or max_uses > 10000:
        raise ValueError("Shared code max uses must be between 1 and 10,000.")
    if not 1 <= count <= 500:
        raise ValueError("Generate between 1 and 500 codes.")
    result = []
    for _ in range(count):
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        obj = AccessCode.objects.create(tenant=tenant, batch=batch, digest=code_digest(raw), role=role,
                                        max_uses=max_uses, expires_at=expires_at, created_by=creator)
        result.append((obj, raw))
    AuditEvent.objects.create(tenant=tenant, actor=creator, action="access_codes_generated",
                              object_type="AccessCode", object_id="bulk",
                              after={"batch_id": batch.pk, "count": len(result), "shared": shared, "role": role})
    return result


def _attempt_key(tenant_id, ip):
    return "join-fail:" + hashlib.sha256(f"{tenant_id}:{ip}".encode()).hexdigest()


def _is_rate_limited(tenant_id, ip):
    return cache.get(_attempt_key(tenant_id, ip), 0) >= 10


def _record_failed_join(tenant_id, ip):
    key = _attempt_key(tenant_id, ip)
    try:
        attempts = cache.incr(key)
    except ValueError:
        cache.add(key, 1, timeout=3600)
        attempts = cache.get(key, 1)
    if attempts >= 10:
        cache.set(key, 10, timeout=3600)


@transaction.atomic
def join_with_code(*, tenant, code, email, display_name, password, ip_address):
    """Consume an access code and create the tenant account/enrollment atomically."""
    if _is_rate_limited(tenant.pk, ip_address):
        return None, "Too many attempts. Try again in an hour."
    subscription = getattr(tenant, "subscription", None)
    if subscription and subscription.status not in ("trial", "active"):
        return None, "This institute is not accepting new enrollments right now. Contact the administrator."
    digest = code_digest(code)
    now = timezone.now()
    access = AccessCode.objects.filter(tenant=tenant, digest=digest, revoked=False,
                                       batch__active=True, used_count__lt=F("max_uses"))
    access = access.filter(models_q_expiry(now)).select_related("batch").first()
    if access is None:
        _record_failed_join(tenant.pk, ip_address)
        return None, "That code is invalid, expired, revoked, or already used."

    # Conditional update protects single-use codes from two simultaneous joins.
    consumed = AccessCode.objects.filter(pk=access.pk, revoked=False, used_count__lt=F("max_uses"))
    consumed = consumed.filter(models_q_expiry(now)).update(used_count=F("used_count") + 1)
    if consumed != 1:
        _record_failed_join(tenant.pk, ip_address)
        return None, "That code has already been used."

    User = get_user_model()
    email = email.strip().lower()
    if User.objects.filter(tenant=tenant, email__iexact=email).exists():
        raise ValueError("An account with this email already exists at this institute. Contact the institute administrator.")
    username = f"{slugify(email)[:80]}-{tenant.slug[:18]}-{uuid.uuid4().hex[:8]}"
    role = access.role
    if role == AccessCode.Role.STUDENT and subscription:
        active_students = User.objects.filter(tenant=tenant, role="student", is_active=True).count()
        if active_students >= subscription.active_student_limit:
            raise ValueError("This institute has reached its student limit. Contact the institute administrator.")
    user = User.objects.create_user(username=username, email=email, first_name=display_name.strip()[:150],
                                    password=password, tenant=tenant, role=role, is_active=True,
                                    is_staff=(role == AccessCode.Role.TEACHER))
    if role == AccessCode.Role.STUDENT:
        BatchEnrollment.objects.create(tenant=tenant, batch=access.batch, student=user)
    else:
        from .models import TeacherBatchAssignment
        TeacherBatchAssignment.objects.create(tenant=tenant, batch=access.batch, teacher=user)
    AuditEvent.objects.create(tenant=tenant, actor=user, action="access_code_consumed",
                              object_type="AccessCode", object_id=str(access.pk),
                              before={"used_count": access.used_count}, after={"used_count": access.used_count + 1})
    AuditEvent.objects.create(tenant=tenant, actor=user, action="tenant_user_joined",
                              object_type="User", object_id=str(user.pk),
                              after={"role": role, "batch_id": access.batch_id})
    return user, None


def models_q_expiry(now):
    from django.db.models import Q
    return Q(expires_at__isnull=True) | Q(expires_at__gt=now)
