"""Tests for embedding matching helpers (pure logic, no API calls)."""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from ai.embeddings import cosine_similarity, find_best_match_sync


def _prod(pid, vec):
    return SimpleNamespace(id=pid, name=f"P{pid}", price=100,
                           photo_caption="", photo_embedding=json.dumps(vec))


class TestCosine:
    def test_identical(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0

    def test_mismatch_dims(self):
        assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0

    def test_empty(self):
        assert cosine_similarity([], [1.0]) == 0.0


class TestFindBestMatch:
    def test_match(self):
        ps = [_prod(1, [1.0, 0.0]), _prod(2, [0.0, 1.0])]
        out = find_best_match_sync("cap", [1.0, 0.0], ps, threshold=0.6)
        assert [r["product"].id for r in out] == [1]

    def test_stale_dims_skipped(self):
        ps = [_prod(1, [1.0, 0.0, 0.5])]
        assert find_best_match_sync("cap", [1.0, 0.0], ps) == []

    def test_corrupt_embedding_skipped(self):
        p = SimpleNamespace(id=9, name="X", price=1, photo_caption="", photo_embedding="{bad json")
        assert find_best_match_sync("cap", [1.0, 0.0], [p]) == []

    def test_no_embedding_field(self):
        p = SimpleNamespace(id=9, name="X", price=1, photo_caption="", photo_embedding=None)
        assert find_best_match_sync("cap", [1.0, 0.0], [p]) == []
