import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from camero.semantic_search import (
    _semantic_db_path,
    search_similar_recordings,
    semantic_recording_is_indexed,
)


class SimilarRecordingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = _semantic_db_path(self.root)
        self.database.parent.mkdir()
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.execute(
            "CREATE TABLE rag_vector_store (record_id TEXT, embeddings BLOB, metadata TEXT)"
        )

    def insert(self, recording_id, components):
        vector = np.zeros(768, dtype="<f4")
        vector[:len(components)] = components
        self.connection.execute(
            "INSERT INTO rag_vector_store VALUES (?, ?, ?)",
            (f"recording_{recording_id}", b"VF32" + vector.tobytes(),
             json.dumps({"title": f"Video {recording_id}", "file_name": "video.mp4", "rel_path": "video.mp4"})),
        )
        self.connection.commit()

    def search(self, **kwargs):
        return search_similar_recordings(
            catalog_root=self.root, recording_id=1,
            settings=SimpleNamespace(semantic_search_min_similarity=0.0), **kwargs,
        )

    def test_actual_index_membership(self):
        self.assertFalse(semantic_recording_is_indexed(self.root, 1))
        self.insert(1, [1, 0])
        self.assertTrue(semantic_recording_is_indexed(self.root, 1))
        self.assertFalse(semantic_recording_is_indexed(self.root, 2))

    def test_cosine_order_threshold_limit_and_source_exclusion(self):
        self.insert(1, [2, 0])
        self.insert(2, [0, 1])
        self.insert(3, [3, 4])
        self.insert(4, [5, 0])
        matches = self.search()
        self.assertEqual([m["recording_id"] for m in matches], [4, 3, 2])
        self.assertAlmostEqual(matches[1]["score"], 0.6)
        self.assertEqual(matches[0]["title"], "Video 4")
        self.assertEqual([m["recording_id"] for m in self.search(min_similarity=0.61)], [4])
        self.assertEqual([m["recording_id"] for m in self.search(limit=1)], [4])
        self.assertEqual(len(self.search(min_similarity=0.0)), 3)

    def test_no_matches_is_empty(self):
        self.insert(1, [1, 0])
        self.insert(2, [0, 1])
        self.assertEqual(self.search(min_similarity=0.9), [])

    def test_missing_source_fails_explicitly(self):
        with self.assertRaisesRegex(RuntimeError, "not semantically indexed"):
            self.search()

    def test_missing_database(self):
        missing = self.root / "missing"
        self.assertFalse(semantic_recording_is_indexed(missing, 1))
        with self.assertRaisesRegex(RuntimeError, "not available"):
            search_similar_recordings(catalog_root=missing, recording_id=1, settings=None)
        self.assertFalse(missing.exists())

    def test_invalid_embedding_fails_explicitly(self):
        self.insert(1, [0, 0])
        with self.assertRaisesRegex(RuntimeError, "Invalid semantic embedding"):
            self.search()
        self.connection.execute("UPDATE rag_vector_store SET embeddings = ?", (b"invalid",))
        self.connection.commit()
        with self.assertRaisesRegex(RuntimeError, "Unsupported semantic embedding format"):
            self.search()
