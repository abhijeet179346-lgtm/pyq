import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from django.test import TestCase

from apps.catalog.models import Chapter, Subject, Topic
from apps.materials import search
from apps.materials.models import MaterialDocument, MaterialSection


class NcertSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        subject = Subject.objects.get_or_create(name='Physics', defaults={'slug': 'physics'})[0]
        topic = Topic.objects.create(chapter=Chapter.objects.create(subject=subject, name='Search ch'), name='Motion')
        doc = MaterialDocument.objects.create(tenant=None, title='NCERT Class 11 Physics: Laws of Motion',
                                              source='https://ncert.nic.in/textbook/pdf/keph105.pdf', license='NCERT')
        cls.inertia = MaterialSection.objects.create(document=doc, topic=topic, title='The law of inertia',
            body='A body continues in its state of rest or uniform motion unless acted upon by a force.')
        cls.momentum = MaterialSection.objects.create(document=doc, topic=topic, title='Momentum',
            body='Momentum is mass times velocity. Force equals the rate of change of momentum.')
        cls.cells = MaterialSection.objects.create(document=doc, topic=topic, title='Electrochemical cells',
            body='A galvanic cell converts chemical energy into electrical energy.')
        cls.sections = MaterialSection.objects.select_related('document', 'topic')

    def test_keyword_search_without_index(self):
        with patch.object(search, 'INDEX_PATH', Path('/nonexistent/index.npz')):
            results = search.search_sections(self.sections, 'law of inertia')
        self.assertEqual(results[0], self.inertia)

    def test_semantic_hits_are_merged_with_keyword_hits(self):
        class FakeEncoder:
            def encode(self, texts, **_):
                return np.array([[1.0, 0.0]], dtype=np.float32)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'index.npz'
            np.savez(path, ids=np.array([self.cells.pk, self.momentum.pk]),
                     vectors=np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
            search._index.cache_clear()
            with patch.object(search, 'INDEX_PATH', path), patch.object(search, 'encoder', FakeEncoder):
                results = search.search_sections(self.sections, 'battery electricity source')
        # No keyword matches "battery", but the vector index finds the cells section.
        self.assertEqual(results[0], self.cells)
        self.assertNotIn(self.momentum, results)  # below the similarity threshold


@unittest.skipUnless((search.MODEL_DIR / 'onnx/model.onnx').exists() and search.INDEX_PATH.exists(),
                     'NCERT model/index not installed on this machine')
class OnnxEncoderTests(unittest.TestCase):
    def test_onnx_vectors_are_normalised_and_comparable(self):
        vectors = search.OnnxEncoder(search.MODEL_DIR).encode(['Newton second law', 'galvanic cell emf'])
        self.assertEqual(vectors.shape, (2, 384))
        np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
