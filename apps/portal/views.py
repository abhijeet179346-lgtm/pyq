from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db import IntegrityError
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods, require_POST
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET
from urllib.parse import urlsplit

from apps.tenancy.models import Tenant
from . import direct
from .forms import CombinedAccessForm, ActivationForm
from .services import activate_account, workspace_ready, create_handoff, consume_handoff
from .verification import send_verification, VerificationDeliveryError, valid_challenge, confirm_verification, password_fingerprint
from .models import StudentFeedback


@login_required
@require_POST
def submit_feedback(request):
    tenant = direct._direct_user(request)
    category = request.POST.get("category", "")
    message = request.POST.get("message", "").strip()
    if category not in StudentFeedback.Category.values:
        return JsonResponse({"error": "Choose a problem or feedback."}, status=400)
    if not message or len(message) > 2000:
        return JsonResponse({"error": "Enter a message of up to 2,000 characters."}, status=400)
    question_id = request.POST.get("question_id", "")
    question = None
    if question_id:
        if not question_id.isascii() or not question_id.isdecimal() or len(question_id) > 18:
            raise Http404()
        question = get_object_or_404(direct.questions(tenant, request.user), pk=int(question_id))
    raw_path = request.POST.get("page_path", "")
    path = raw_path.split("?", 1)[0].split("#", 1)[0] if raw_path.startswith("/") and not raw_path.startswith("//") else ""
    feedback = StudentFeedback.objects.create(tenant=tenant, reporter=request.user, category=category,
                                              message=message, question=question, page_path=path[:255])
    from .feedback_notifications import notify_feedback
    sent = notify_feedback(feedback)
    return JsonResponse({"notification_sent": sent, "message": "Thanks for letting us know." if sent else
                         "Your feedback was saved, but the email alert could not be sent."})


class PortalLoginView(LoginView):
    template_name = "portal/login.html"
    authentication_form = CombinedAccessForm

    def get_success_url(self):
        if not self.request.user.email_verified:
            target = super().get_success_url()
            if urlsplit(target).path.startswith("/verify-email/confirm/"):
                return target
            from django.urls import reverse
            return reverse("verify_email")
        if self.request.user.is_superuser and not getattr(self.request, "is_core_domain", False):
            return "/admin/"
        return super().get_success_url()

    def form_valid(self, form):
        response = super().form_valid(form)
        if not self.request.user.email_verified:
            try:
                user = self.request.user
                outstanding = user.email_verifications.filter(email=user.email,
                    password_fingerprint=password_fingerprint(user), consumed_at__isnull=True,
                    expires_at__gt=timezone.now()).exists()
                if not outstanding:
                    messages.info(self.request, send_verification(user))
            except VerificationDeliveryError:
                messages.error(self.request, "We couldn't send your verification email. Please try again shortly.")
        return response

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "tenant", None) is None and not getattr(request, "is_core_domain", False):
            raise Http404("Open your institute's subdomain to sign in.")
        return super().dispatch(request, *args, **kwargs)


@require_GET
def home(request):
    tenant = getattr(request, "tenant", None)
    if getattr(request, "is_core_domain", False) or tenant is None:
        if not getattr(request, "is_core_domain", False):
            raise Http404("Institute not found.")
        if request.user.is_authenticated:
            return direct.dashboard(request)
        return render(request, "portal/login.html", {"form": CombinedAccessForm(request), "next": ""})
    if not request.user.is_authenticated:
        return render(request, "portal/login.html", {"form": CombinedAccessForm(request), "next": ""})
    if (request.user.tenant_id in (None, tenant.pk) or (request.user.tenant_id and request.user.tenant.is_master)) and not workspace_ready(request.user, tenant):
        return redirect("activate_account")
    return direct.dashboard(request)


@login_required
@require_http_methods(["GET", "POST"])
def activate(request):
    tenant = getattr(request, "tenant", None)
    if getattr(request, "is_core_domain", False) or tenant is None:
        if not getattr(request, "is_core_domain", False):
            raise Http404()
        return redirect("portal_home")
    if tenant and request.user.tenant_id not in (None, tenant.pk) and not request.user.tenant.is_master:
        raise PermissionDenied("Sign in with an account for this institute.")
    form = ActivationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            activated_user, error = activate_account(user=request.user, code=form.cleaned_data["code"],
                tenant=tenant, ip_address=request.META.get("REMOTE_ADDR", "unknown"))
            if error:
                form.add_error("code", error)
            elif tenant:
                if activated_user.pk != request.user.pk:
                    login(request, activated_user, backend="apps.accounts.backends.TenantAwareBackend")
                messages.success(request, "Your access code is activated. Welcome to your workspace.")
                return redirect("portal_home")
            else:
                return redirect(create_handoff(request.user))
        except IntegrityError:
            form.add_error("code", "This account is already registered at that institute. Sign in on its subdomain.")
    return render(request, "portal/activate.html", {"form": form})


@require_http_methods(["GET", "POST"])
def signup(request):
    # Old registration links now open the same combined entry point.
    if getattr(request, "tenant", None) is None and not getattr(request, "is_core_domain", False):
        raise Http404()
    return redirect("portal_login")


@login_required
@require_POST
def open_workspace(request):
    if not getattr(request, "is_core_domain", False) or not request.user.is_superuser:
        raise PermissionDenied()
    try:
        target = None
        if request.user.is_superuser and request.POST.get("tenant"):
            target = get_object_or_404(Tenant, pk=request.POST["tenant"], status="active")
        return redirect(create_handoff(request.user, tenant=target))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("activate_account")


@require_GET
def continue_login(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404()
    user = consume_handoff(request.GET.get("token", ""), tenant)
    if user is None:
        messages.error(request, "This sign-in link has expired or was already used. Sign in again.")
        return redirect("portal_login")
    login(request, user, backend="apps.accounts.backends.TenantAwareBackend")
    response = redirect("portal_home" if workspace_ready(user, tenant) else "activate_account")
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_GET
def verify_email(request):
    if request.user.email_verified:
        return redirect("portal_home")
    return render(request, "portal/verify_email.html")


@login_required
@require_POST
def resend_verification(request):
    try:
        messages.info(request, send_verification(request.user))
    except VerificationDeliveryError:
        messages.error(request, "We couldn't send your verification email. Please try again shortly.")
    return redirect("verify_email")


@login_required
@require_http_methods(["GET", "POST"])
def verify_email_confirm(request, token):
    if request.method == "POST":
        if confirm_verification(request.user, token):
            messages.success(request, "Email verified. You can now continue.")
            response = redirect("portal_home")
        else:
            messages.error(request, "This verification link is invalid, expired, or already used. Request a new email.")
            response = redirect("verify_email")
    else:
        response = render(request, "portal/verify_email_confirm.html", {
            "valid_link": valid_challenge(request.user, token) is not None})
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
