import base64
import hashlib
import secrets
import time
import uuid
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import redirect
from django.utils.crypto import constant_time_compare
from django.utils import timezone
from django.views.decorators.http import require_GET
from google.auth.transport.requests import Request
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import id_token

from apps.accounts.models import User
from apps.tenancy.features import is_enabled
from apps.tenancy.models import Tenant
from .services import create_handoff


def google_configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def _google_account(claims, tenant):
    tenant = tenant or Tenant.objects.filter(is_master=True).first()
    subject = str(claims["sub"])
    email = claims["email"].strip().lower()
    candidates = User.objects.filter(tenant=tenant)
    user = candidates.filter(google_subject=subject).first()
    if user is None and tenant is None:
        enrolled = list(User.objects.filter(google_subject=subject, is_active=True)[:2])
        if len(enrolled) == 1:
            user = enrolled[0]
    if user is None:
        user = candidates.filter(email__iexact=email).first()
        if user is None and tenant is None:
            existing = list(User.objects.filter(email__iexact=email)[:2])
            if len(existing) == 1:
                user = existing[0]
        if user:
            if user.google_subject and user.google_subject != subject:
                raise ValueError("This account is connected to another Google identity.")
            # Google is authoritative for Gmail or a verified Workspace domain.
            if not (email.endswith("@gmail.com") or claims.get("hd")):
                raise ValueError("Sign in with your password for this existing account.")
        else:
            user = User.objects.create_user(username="google-" + uuid.uuid4().hex,
                email=email, first_name=str(claims.get("given_name", ""))[:150],
                tenant=tenant, role="student", password=None)
        user.google_subject = subject
        user.save(update_fields=["google_subject"])
    if not user.is_active:
        raise ValueError("This account is inactive. Contact your institute.")
    if user.email != email:
        raise ValueError("This Google account's email no longer matches the registered address.")
    if not user.email_verified:
        # An unverified password must not survive a verified Google claim to
        # the same mailbox, as it may have been chosen by somebody else.
        if user.has_usable_password():
            user.set_unusable_password()
        user.email_verified_at = timezone.now()
        user.save(update_fields=["password", "email_verified_at"])
        user.email_verifications.filter(consumed_at__isnull=True).update(consumed_at=timezone.now())
    return user


@require_GET
def google_start(request):
    tenant = getattr(request, "tenant", None)
    if tenant and not getattr(request, "is_core_domain", False):
        if not is_enabled(tenant, "google_login"):
            messages.error(request, "Google sign-in is not enabled for this institute.")
            return redirect("portal_login")
        return redirect(settings.PYQ_CORE_ORIGIN + "/auth/google/start/?" + urlencode({"tenant": tenant.slug}))
    if not getattr(request, "is_core_domain", False):
        raise Http404()
    if tenant and not is_enabled(tenant, "google_login"):
        messages.error(request, "Google sign-in is disabled for the core platform.")
        return redirect("portal_login")
    if not google_configured():
        messages.error(request, "Google sign-in is not configured yet. Please use email and password.")
        return redirect("portal_login")
    slug = request.GET.get("tenant", "")
    if slug:
        tenant = Tenant.objects.filter(slug=slug, is_master=False, status="active").first()
        if tenant is None or not is_enabled(tenant, "google_login"):
            raise Http404()
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    request.session["google_login"] = {"state": state, "nonce": nonce,
        "verifier": verifier, "tenant": tenant.pk if tenant else None, "started": time.time()}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {"client_id": settings.GOOGLE_CLIENT_ID, "redirect_uri": settings.PYQ_CORE_ORIGIN + "/auth/google/callback/",
        "response_type": "code", "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@require_GET
def google_callback(request):
    if not getattr(request, "is_core_domain", False):
        raise Http404()
    flow = request.session.pop("google_login", None)
    if not flow or not constant_time_compare(flow.get("state", ""), request.GET.get("state", "")) or time.time() - flow.get("started", 0) > 600:
        messages.error(request, "Google sign-in expired or could not be verified. Please try again.")
        return redirect("portal_login")
    if request.GET.get("error") or not request.GET.get("code") or not google_configured():
        messages.error(request, "Google sign-in was cancelled or is unavailable.")
        return redirect("portal_login")
    try:
        tenant = Tenant.objects.filter(is_master=True).first()
        if flow["tenant"]:
            tenant = Tenant.objects.filter(pk=flow["tenant"], status="active").first()
            if tenant is None or not is_enabled(tenant, "google_login"):
                raise ValueError("Institute Google sign-in is unavailable.")
        if tenant is None or not is_enabled(tenant, "google_login"):
            raise ValueError("Google sign-in is unavailable.")
        response = requests.post("https://oauth2.googleapis.com/token", timeout=15, data={
            "code": request.GET["code"], "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.PYQ_CORE_ORIGIN + "/auth/google/callback/",
            "grant_type": "authorization_code", "code_verifier": flow["verifier"],
        })
        response.raise_for_status()
        claims = id_token.verify_oauth2_token(response.json()["id_token"], Request(), settings.GOOGLE_CLIENT_ID)
        if not constant_time_compare(str(claims.get("nonce", "")), flow["nonce"]) or claims.get("email_verified") is not True:
            raise ValueError("Google did not verify this sign-in or email address.")
        if not claims.get("email") or not claims.get("sub"):
            raise ValueError("Google did not return the required account information.")
        with transaction.atomic():
            user = _google_account(claims, tenant)
        login(request, user, backend="apps.accounts.backends.TenantAwareBackend")
        if tenant and not tenant.is_master:
            return redirect(create_handoff(user))
        return redirect("portal_home")
    except (requests.RequestException, GoogleAuthError, ValueError, KeyError, IntegrityError):
        messages.error(request, "Google sign-in could not be completed. Try again or use email and password.")
        return redirect("portal_login")
