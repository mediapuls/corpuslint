import hashlib
import pytest


class FakeEmbedder:
    """Deterministic 8-dim embedder. Same text -> same vector.
    Optional `overrides` maps exact text -> vector for cluster control."""

    def __init__(self, overrides: dict[str, list[float]] | None = None):
        self.overrides = overrides or {}

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            if t in self.overrides:
                out.append(self.overrides[t])
                continue
            h = hashlib.sha256(t.encode()).digest()[:8]
            out.append([b / 255.0 for b in h])
        return out


@pytest.fixture
def fake_embedder():
    return FakeEmbedder
