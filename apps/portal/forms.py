import uuid

from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q


class CombinedAccessForm(forms.Form):
    email = forms.CharField(max_length=254, label="Email or username", widget=forms.TextInput(attrs={"autocomplete": "username"}))
    password = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    error_message = "We couldn't sign you in with those details. Check your password or continue with Google. If your email is used at multiple institutes, sign in on your institute's subdomain."

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        email = data.get("email", "").strip().lower()
        password = data.get("password")
        if not email or not password:
            return data
        User = get_user_model()
        self.user_cache = authenticate(self.request, username=email, password=password)
        if self.user_cache is not None:
            return data
        # A failed password, disabled account, or Google-only account must never
        # become a new account or have its password replaced.
        existing = User.objects.filter(email__iexact=email)
        if getattr(self.request, "is_core_domain", False):
            existing = existing.filter(Q(tenant=getattr(self.request, "tenant", None)) | Q(tenant__isnull=True))
        if existing.exists():
            raise forms.ValidationError(self.error_message)
        try:
            validate_email(email)
        except ValidationError:
            raise forms.ValidationError("Enter an existing username or a valid email address to create an account.")
        tenant = getattr(self.request, "tenant", None)
        user = User(username="account-" + uuid.uuid4().hex, email=email,
                    tenant=tenant, role="student", is_staff=False)
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            self.add_error("password", exc)
            return data
        try:
            with transaction.atomic():
                user.set_password(password)
                user.save()
        except IntegrityError:
            # Another request may have registered this email at the same time.
            self.user_cache = authenticate(self.request, username=email, password=password)
            if self.user_cache is None:
                raise forms.ValidationError(self.error_message)
        else:
            user.backend = "apps.accounts.backends.TenantAwareBackend"
            self.user_cache = user
        return data

    def get_user(self):
        return self.user_cache


class ActivationForm(forms.Form):
    code = forms.CharField(min_length=8, max_length=8, label="Access code",
        widget=forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "characters", "placeholder": "Enter your 8-character code"}))
