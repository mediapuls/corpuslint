from corpuslint.config import Config
from corpuslint.models import Chunk
from corpuslint.checks.base import CheckContext
from corpuslint.checks.near_duplicates import NearDuplicatesCheck


def test_returns_empty_without_embeddings():
    ctx = CheckContext(chunks=[Chunk("0", "a", "s")], embeddings=None, config=Config())
    assert NearDuplicatesCheck().run(ctx) == []


def test_flags_near_duplicate_pairs():
    chunks = [Chunk("0", "cats are nice", "s"), Chunk("1", "cats are pleasant", "s"), Chunk("2", "quantum physics", "s")]
    embeddings = [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0]]
    ctx = CheckContext(chunks=chunks, embeddings=embeddings, config=Config(near_dupe_threshold=0.95))
    findings = NearDuplicatesCheck().run(ctx)
    assert len(findings) == 1
    assert set(findings[0].chunk_ids) == {"0", "1"}
