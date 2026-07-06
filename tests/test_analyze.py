from pathlib import Path
from corpuslint.config import Config
from corpuslint.analyze import analyze
from tests.conftest import FakeEmbedder


def test_analyze_end_to_end_flags_duplicate(tmp_path: Path):
    (tmp_path / "a.md").write_text("Cats are great pets.")
    (tmp_path / "b.md").write_text("Cats are great pets.")
    cfg = Config(target_chunk_tokens=2, low_info_min_tokens=1, min_chunk_tokens=1, enabled_checks=["exact_duplicates"])
    report = analyze([str(tmp_path)], cfg, embedder=FakeEmbedder())
    assert report.total_chunks == 2
    assert "exact_duplicates" in report.findings_by_check()
    assert report.score < 100
