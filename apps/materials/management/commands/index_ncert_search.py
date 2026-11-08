from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.materials.models import MaterialSection


class Command(BaseCommand):
    help = 'Build local semantic vectors for the shared NCERT library.'

    def handle(self, *args, **options):
        import numpy as np
        from apps.materials.search import encoder
        sections = list(MaterialSection.objects.filter(active=True, document__active=True,
            document__tenant__isnull=True, document__source__startswith='https://ncert.nic.in/textbook/pdf/')
            .select_related('document', 'topic').order_by('pk'))
        if not sections:
            raise CommandError('Import the NCERT books first.')
        model = encoder()
        texts = [s.document.title + '\n' + s.title + '\n' + s.body for s in sections]
        vectors = model.encode(texts, batch_size=32, show_progress_bar=True, normalize_embeddings=True)
        dest = Path(settings.BASE_DIR) / 'data/ncert-search-index.npz'
        temp = dest.with_name('ncert-search-index.tmp.npz')
        np.savez_compressed(temp, ids=np.array([s.pk for s in sections], dtype=np.int64), vectors=vectors.astype(np.float32))
        temp.replace(dest)
        self.stdout.write(self.style.SUCCESS(f'Indexed {len(sections)} NCERT sections.'))
