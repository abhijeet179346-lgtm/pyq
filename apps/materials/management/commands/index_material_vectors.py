import json
import os
import urllib.request

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.materials.models import MaterialSection
from apps.tutoring.models import AIUsage, TenantAISettings


class Command(BaseCommand):
    help = "Create Pinecone vectors for active study-material sections."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int)
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        host = os.environ.get("PINECONE_INDEX_HOST", "").rstrip("/")
        pinecone_key = os.environ.get("PINECONE_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")
        if not all((host, pinecone_key, openai_key)):
            raise CommandError("Set PINECONE_INDEX_HOST, PINECONE_API_KEY and OPENAI_API_KEY.")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        model = os.environ.get("PYQ_EMBEDDING_MODEL", "text-embedding-3-small")
        sections = MaterialSection.objects.filter(active=True, document__active=True)
        if options["tenant"]:
            sections = sections.filter(document__tenant_id=options["tenant"])
        sections = list(sections.select_related("document", "topic")[:options["limit"]])
        if not sections:
            self.stdout.write("No active study-material sections found.")
            return
        if options["tenant"]:
            from django.db.models import Sum
            from django.utils import timezone
            ai = TenantAISettings.objects.filter(tenant_id=options["tenant"]).first()
            if ai is None or ai.monthly_budget_usd <= 0:
                raise CommandError("Set a monthly AI budget for the tenant before vector indexing.")
            month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            spent = AIUsage.objects.filter(tenant_id=options["tenant"], created_at__gte=month_start).aggregate(value=Sum("cost_usd"))["value"] or 0
            if spent >= ai.monthly_budget_usd:
                raise CommandError("Tenant has reached its monthly AI budget.")
        body = {"model": model, "input": [section.title + "\n" + section.body[:10000] for section in sections]}
        request = urllib.request.Request(base + "/embeddings", data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + openai_key, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read())
        if options["tenant"]:
            from apps.tutoring.services import record_ai_usage
            record_ai_usage(tenant=ai.tenant, user=None, operation="material_embedding", model=model,
                input_tokens=int(result.get("usage", {}).get("prompt_tokens", 0)))
        namespaces = {}
        for section, embedded in zip(sections, result["data"]):
            tenant_id = section.document.tenant_id
            namespace = "global" if tenant_id is None else f"tenant-{tenant_id}"
            namespaces.setdefault(namespace, []).append({"id": str(section.pk), "values": embedded["embedding"],
                "metadata": {"section_id": section.pk, "topic_id": section.topic_id,
                             "tenant_id": tenant_id, "document": section.document.title}})
            section.vector_indexed_at = timezone.now()
            section.save(update_fields=["vector_indexed_at"])
        for namespace, vectors in namespaces.items():
            payload = {"namespace": namespace, "vectors": vectors}
            request = urllib.request.Request(host + "/vectors/upsert", data=json.dumps(payload).encode(),
                headers={"Api-Key": pinecone_key, "Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=120) as response:
                response.read()
        self.stdout.write(self.style.SUCCESS(f"Indexed {len(sections)} sections using {model}."))
