from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from apps.catalog.models import Subject
from .models import AccessCode, Batch


class JoinForm(forms.Form):
    code = forms.CharField(min_length=8, max_length=8, strip=True)
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField(max_length=254)
    password = forms.CharField(widget=forms.PasswordInput, min_length=12)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("confirm_password") and data["password"] != data["confirm_password"]:
            self.add_error("confirm_password", "Passwords do not match.")
        if data.get("password"):
            validate_password(data["password"])
        return data


class AccessCodeIssueForm(forms.Form):
    batch = forms.ModelChoiceField(queryset=Batch.objects.none())
    role = forms.ChoiceField(choices=AccessCode.Role.choices)
    shared = forms.BooleanField(required=False, label="One shared classroom code")
    count = forms.IntegerField(min_value=1, max_value=500, initial=1, help_text="Number of single-use codes")
    max_uses = forms.IntegerField(min_value=1, max_value=10000, initial=30, help_text="Maximum registrations for a shared code")
    expires_at = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))

    def __init__(self, *args, tenant, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["batch"].queryset = Batch.objects.filter(tenant=tenant, active=True)


class TeacherAssignmentForm(forms.Form):
    teacher = forms.ModelChoiceField(queryset=get_user_model().objects.none())
    subject = forms.ModelChoiceField(queryset=Subject.objects.all())
    batch = forms.ModelChoiceField(queryset=Batch.objects.none(), required=False)

    def __init__(self, *args, tenant, **kwargs):
        from django.contrib.auth import get_user_model
        super().__init__(*args, **kwargs)
        self.fields["teacher"].queryset = get_user_model().objects.filter(tenant=tenant, role="teacher")
        self.fields["subject"].queryset = Subject.objects.order_by("name")
        self.fields["batch"].queryset = Batch.objects.filter(tenant=tenant, active=True)
