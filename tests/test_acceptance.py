from corpuslint.config import Config
from corpuslint.analyze import analyze
from tests.conftest import FakeEmbedder


def test_acceptance_catches_planted_issues():
    cfg = Config(
        target_chunk_tokens=1000,      # keep each file as one chunk
        low_info_min_tokens=5,
        min_chunk_tokens=5,
        enabled_checks=["exact_duplicates", "low_information", "chunk_size"],
    )
    report = analyze(["tests/fixtures/corpus"], cfg, embedder=FakeEmbedder())
    by_check = report.findings_by_check()
    assert "exact_duplicates" in by_check          # good.md == dup.md
    assert "low_information" in by_check            # thin.md + symbols.md
    thin_flagged = {cid for f in by_check["low_information"] for cid in f.chunk_ids}
    assert len(thin_flagged) >= 2
    assert report.score < 100
