"""Search visible study sections using keyword and optional local semantic vectors."""
import logging
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db.models import Case, IntegerField, Q, Value, When

logger = logging.getLogger(__name__)
WORDS = re.compile(r'[a-z0-9]+')
STOP = {'the', 'and', 'for', 'what', 'how', 'are', 'with', 'from', 'about', 'explain', 'class', 'chapter', 'physics', 'chemistry'}


MODEL_DIR = Path(settings.BASE_DIR) / 'data/models/all-MiniLM-L6-v2'


class OnnxEncoder:
    """all-MiniLM-L6-v2 on ONNX Runtime: mean pooling + L2 norm, same vectors as sentence-transformers.

    Used on the server, where PyTorch is too large for the 1 GB instance.
    """
    def __init__(self, model_dir, max_length=512):
        import onnxruntime
        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_file(str(model_dir / 'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length)
        self.tokenizer.enable_padding()
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(model_dir / 'onnx/model.onnx'), options,
                                                    providers=['CPUExecutionProvider'])

    def encode(self, texts, batch_size=32, normalize_embeddings=True, **_):
        import numpy as np
        out = []
        for start in range(0, len(texts), batch_size):
            batch = self.tokenizer.encode_batch(list(texts[start:start + batch_size]))
            ids = np.array([e.ids for e in batch], dtype=np.int64)
            mask = np.array([e.attention_mask for e in batch], dtype=np.int64)
            types = np.array([e.type_ids for e in batch], dtype=np.int64)
            hidden = self.session.run(None, {'input_ids': ids, 'attention_mask': mask, 'token_type_ids': types})[0]
            weights = mask[..., None].astype(np.float32)
            vectors = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
            if normalize_embeddings:
                vectors /= np.clip(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12, None)
            out.append(vectors.astype(np.float32))
        return np.concatenate(out) if out else np.zeros((0, 384), dtype=np.float32)


@lru_cache(maxsize=1)
def encoder():
    if (MODEL_DIR / 'onnx/model.onnx').exists():
        try:
            return OnnxEncoder(MODEL_DIR)
        except ImportError:
            pass
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(str(MODEL_DIR), local_files_only=True)


INDEX_PATH = Path(settings.BASE_DIR) / 'data/ncert-search-index.npz'


@lru_cache(maxsize=2)
def _index(mtime):
    """Load the vector index once per file version (keyed by modification time)."""
    import numpy as np
    with np.load(INDEX_PATH, allow_pickle=False) as data:
        return data['ids'], data['vectors']


def semantic_candidates(query):
    """Return vector ranks for the shared NCERT corpus, if an index is installed."""
    if not INDEX_PATH.exists():
        return []
    try:
        import numpy as np
        ids, vectors = _index(INDEX_PATH.stat().st_mtime)
        query_vector = encoder().encode([query], normalize_embeddings=True)[0]
        scores = vectors @ query_vector
        top = np.argsort(scores)[-120:][::-1]
        return [(int(ids[i]), float(scores[i])) for i in top if scores[i] >= 0.22]
    except Exception:
        logger.exception('NCERT semantic search unavailable; using keyword search')
        return []


def search_sections(sections, query):
    words = list(dict.fromkeys(w for w in WORDS.findall(query.lower()) if len(w) > 1 and w not in STOP))[:12]
    if not words:
        words = WORDS.findall(query.lower())[:12]
    if not words:
        return []
    condition = Q(pk__in=[])
    for word in words:
        condition |= Q(title__icontains=word) | Q(body__icontains=word) | Q(topic__name__icontains=word) | Q(document__title__icontains=word)
    weight = Value(0, output_field=IntegerField())
    for word in words:
        weight += Case(When(title__icontains=word, then=Value(5)), default=Value(0), output_field=IntegerField())
        weight += Case(When(body__icontains=word, then=Value(1)), default=Value(0), output_field=IntegerField())
        weight += Case(When(topic__name__icontains=word, then=Value(3)), default=Value(0), output_field=IntegerField())
    lexical = list(sections.filter(condition).annotate(search_weight=weight).order_by('-search_weight', 'pk')[:1500])
    dense = semantic_candidates(query)
    dense_rank = {pk: rank for rank, (pk, _) in enumerate(dense, 1)}
    dense_rows = sections.filter(pk__in=dense_rank)
    candidates = {s.pk: s for s in lexical}
    candidates.update({s.pk: s for s in dense_rows})
    phrase = ' '.join(words)
    def keyword_score(section):
        title = (section.title + ' ' + section.topic.name + ' ' + section.document.title).lower()
        body = section.body.lower()
        return (sum(5 * (w in title) + min(body.count(w), 4) for w in words)
                + 8 * (phrase in title) + 5 * (phrase in body)
                + 3 * sum(w in title or w in body for w in words) / len(words))
    lexical_rank = {s.pk: rank for rank, s in enumerate(sorted(lexical, key=keyword_score, reverse=True), 1)}
    def score(section):
        return (1 / (30 + lexical_rank[section.pk]) if section.pk in lexical_rank else 0) + (1 / (30 + dense_rank[section.pk]) if section.pk in dense_rank else 0) + keyword_score(section) / 10000
    return sorted(candidates.values(), key=score, reverse=True)
