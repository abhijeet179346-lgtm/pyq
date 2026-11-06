import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from apps.accounts.models import EmailVerification, User
from .services import tenant_origin


class VerificationDeliveryError(Exception):
    pass


def password_fingerprint(user):
    return hashlib.sha256(user.password.encode()).hexdigest()


@transaction.atomic
def send_verification(user):
    user = User.objects.select_for_update().get(pk=user.pk)
    if user.email_verified:
        return "Your email is already verified."
    now = timezone.now()
    recent = user.email_verifications.order_by("-created_at").first()
    if recent and recent.created_at > now - timedelta(seconds=60):
        return "Please wait a minute before requesting another email."
    if user.email_verifications.filter(created_at__gt=now - timedelta(days=1)).count() >= 10:
        return "You've reached today's email limit. Please try again tomorrow."
    raw = secrets.token_urlsafe(32)
    origin = tenant_origin(user.tenant) if user.tenant_id else settings.PYQ_CORE_ORIGIN
    link = origin + "/verify-email/confirm/" + raw + "/"
    # Invalidate older links only if this email is successfully handed to the backend.
    user.email_verifications.filter(consumed_at__isnull=True).update(consumed_at=now)
    EmailVerification.objects.create(token_digest=hashlib.sha256(raw.encode()).hexdigest(),
        user=user, email=user.email, password_fingerprint=password_fingerprint(user),
        expires_at=now + timedelta(hours=1))
    try:
        sent = send_mail("Verify your email for PYQNet",
            "Confirm your email address to continue to PYQNet.\n\n"
            + link + "\n\nThis link expires in one hour. Sign in with the same account, then confirm your email. "
            "If you did not request this, ignore this message.",
            settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
        if sent != 1:
            raise VerificationDeliveryError()
    except Exception as exc:
        raise VerificationDeliveryError() from exc
    return "Verification email sent. Check your inbox and spam folder."


def valid_challenge(user, raw):
    challenge = EmailVerification.objects.filter(token_digest=hashlib.sha256(raw.encode()).hexdigest(),
        user=user, email=user.email, consumed_at__isnull=True,
        expires_at__gt=timezone.now()).first()
    if challenge and constant_time_compare(challenge.password_fingerprint, password_fingerprint(user)):
        return challenge
    return None


@transaction.atomic
def confirm_verification(user, raw):
    current = User.objects.select_for_update().get(pk=user.pk)
    if not current.is_active:
        return False
    challenge = valid_challenge(current, raw)
    if challenge is None:
        return False
    now = timezone.now()
    consumed = EmailVerification.objects.filter(pk=challenge.pk, consumed_at__isnull=True,
        expires_at__gt=now).update(consumed_at=now)
    if consumed != 1:
        return False
    current.email_verified_at = now
    current.save(update_fields=["email_verified_at"])
    user.email_verified_at = now
    current.email_verifications.filter(consumed_at__isnull=True).update(consumed_at=now)
    return True
