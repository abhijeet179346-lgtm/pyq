from pathlib import Path
from hashlib import sha256

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
        texts = [s.document.title + '\n' + s.title + '\n' + s.body for s in sections]
        dest = Path(settings.BASE_DIR) / 'data/ncert-search-index.npz'
        temp = dest.with_name('ncert-search-index.tmp.npz')
        signatures = [sha256(text.encode('utf-8')).hexdigest() for text in texts]
        cached = {}
        if dest.exists():
            with np.load(dest, allow_pickle=False) as previous:
                previous_ids = previous['ids']
                previous_vectors = previous['vectors']
                previous_signatures = previous['signatures'] if 'signatures' in previous.files else None
                for index, section_id in enumerate(previous_ids):
                    # Indexes created before signatures were stored are treated as
                    # unchanged once; subsequent runs can detect edited content.
                    signature = previous_signatures[index] if previous_signatures is not None else None
                    cached[int(section_id)] = (previous_vectors[index], str(signature) if signature is not None else None)
        missing = [i for i, section in enumerate(sections)
                   if section.pk not in cached or cached[section.pk][1] not in (None, signatures[i])]
        if missing:
            model = encoder()
            fresh = model.encode([texts[i] for i in missing], batch_size=32,
                                 show_progress_bar=True, normalize_embeddings=True).astype(np.float32)
            for index, vector in zip(missing, fresh):
                cached[sections[index].pk] = (vector, signatures[index])
        vectors = np.stack([cached[section.pk][0] for section in sections]).astype(np.float32)
        np.savez_compressed(temp, ids=np.array([s.pk for s in sections], dtype=np.int64),
                            vectors=vectors, signatures=np.array(signatures, dtype='U64'))
        temp.replace(dest)
        self.stdout.write(self.style.SUCCESS(f'Indexed {len(sections)} NCERT sections.'))
