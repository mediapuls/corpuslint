from corpuslint.config import Config
from corpuslint.models import Chunk
from corpuslint.checks.base import CheckContext
from corpuslint.checks.embedding_outliers import EmbeddingOutliersCheck


def test_flags_the_far_outlier():
    chunks = [Chunk(str(i), f"t{i}", "s") for i in range(6)]
    embeddings = [[1.0, 0.0]] * 5 + [[-1.0, 0.0]]  # last one is opposite
    ctx = CheckContext(chunks, embeddings, Config(outlier_zscore=1.5))
    findings = EmbeddingOutliersCheck().run(ctx)
    assert {cid for f in findings for cid in f.chunk_ids} == {"5"}


def test_empty_when_too_few_chunks():
    ctx = CheckContext([Chunk("0", "a", "s")], [[1.0, 0.0]], Config())
    assert EmbeddingOutliersCheck().run(ctx) == []
