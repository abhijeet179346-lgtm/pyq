from django import forms
from .models import Tenant


class TenantBrandingForm(forms.ModelForm):
    class Meta:
        model = Tenant
        fields = ["display_name", "primary_color", "logo_url", "welcome_text"]
        widgets = {"welcome_text": forms.Textarea(attrs={"rows": 3})}

    def clean_primary_color(self):
        color = self.cleaned_data["primary_color"].strip()
        import re
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise forms.ValidationError("Enter a six-digit hex color such as #1759a6.")
        return color.lower()
