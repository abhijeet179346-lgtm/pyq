from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.features.models import TenantSubscription


class Command(BaseCommand):
    help = "Move expired trials and subscriptions into a 30-day read-only grace, then suspend tenants."

    def handle(self, *args, **options):
        today = timezone.localdate()
        changed = 0
        for sub in TenantSubscription.objects.select_related("tenant").all():
            if sub.status == TenantSubscription.Status.TRIAL and sub.trial_ends_at and sub.trial_ends_at.date() < today:
                sub.status = TenantSubscription.Status.GRACE
                sub.grace_ends_at = sub.trial_ends_at.date() + timedelta(days=30)
            elif sub.status == TenantSubscription.Status.ACTIVE and sub.paid_through and sub.paid_through < today:
                sub.status = TenantSubscription.Status.GRACE
                sub.grace_ends_at = sub.paid_through + timedelta(days=30)
            elif sub.status == TenantSubscription.Status.GRACE and sub.grace_ends_at and sub.grace_ends_at < today:
                sub.status = TenantSubscription.Status.SUSPENDED
                sub.tenant.status = "suspended"
                sub.tenant.save(update_fields=["status"])
            else:
                continue
            sub.save(update_fields=["status", "grace_ends_at", "updated_at"])
            AuditEvent.objects.create(tenant=sub.tenant, actor=None, action="tenant_subscription_status_changed",
                object_type="TenantSubscription", object_id=str(sub.pk), after={"status": sub.status,
                "grace_ends_at": sub.grace_ends_at.isoformat() if sub.grace_ends_at else None})
            changed += 1
        self.stdout.write(self.style.SUCCESS(f"Updated {changed} tenant subscriptions."))
