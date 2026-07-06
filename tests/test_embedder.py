from corpuslint.config import Config
from corpuslint.embedder import get_embedder


def test_get_embedder_none_returns_none():
    assert get_embedder("none", Config()) is None


def test_fake_embedder_is_deterministic(fake_embedder):
    emb = fake_embedder()
    a = emb.embed(["same", "same"])
    assert a[0] == a[1]
    assert len(a[0]) == 8
