import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

RESERVED_SLUGS = {"www", "app", "api", "admin", "static", "mail"}


class Tenant(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    name = models.CharField(max_length=180)
    is_master = models.BooleanField(default=False, editable=False)
    slug = models.SlugField(max_length=63, unique=True)
    display_name = models.CharField(max_length=180, blank=True)
    primary_color = models.CharField(max_length=7, default="#1759a6")
    logo_url = models.URLField(blank=True)
    welcome_text = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["is_master"], condition=models.Q(is_master=True), name="single_master_tenant")]

    def clean(self):
        self.slug = slugify(self.slug or self.name)
        if self.slug in RESERVED_SLUGS:
            raise ValidationError({"slug": "This subdomain is reserved."})
        if self.primary_color and not re.fullmatch(r"#[0-9a-fA-F]{6}", self.primary_color):
            raise ValidationError({"primary_color": "Use a hex color such as #1759a6."})

    def __str__(self):
        return self.name


