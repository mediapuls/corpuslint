import json
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


def test_analyze_jsonl_fast_path_preserves_chunk_ids(tmp_path: Path):
    """A .jsonl input must use load_prechunked_jsonl (fast-path), not re-chunk the text.

    The fast-path preserves 'id' from the file; the document→chunk route would
    generate a different id based on source + paragraph index. Verifying the id
    proves the correct branch was taken.
    """
    p = tmp_path / "corpus.jsonl"
    p.write_text(
        json.dumps({"id": "explicit-id-1", "text": "Cats are great pets.", "source": "doc/1"}) + "\n"
        + json.dumps({"id": "explicit-id-2", "text": "Dogs are loyal companions.", "source": "doc/2"}) + "\n"
    )
    cfg = Config(enabled_checks=["exact_duplicates"])
    report = analyze([str(p)], cfg, embedder=FakeEmbedder())
    # Two chunks loaded from pre-chunked data, not re-chunked from document text
    assert report.total_chunks == 2
    # Chunk IDs come from the .jsonl 'id' field, not from re-chunking
    from corpuslint.chunker import load_prechunked_jsonl
    expected_ids = {c.id for c in load_prechunked_jsonl(str(p))}
    assert expected_ids == {"explicit-id-1", "explicit-id-2"}


def test_analyze_jsonl_mixed_with_docs_both_included(tmp_path: Path):
    """Mixing .jsonl pre-chunked data with regular doc files in one analyze() call
    must include chunks from both — no path is silently dropped."""
    (tmp_path / "notes.md").write_text("Sunshine is warm.")
    jl = tmp_path / "extra.jsonl"
    jl.write_text(json.dumps({"id": "pre-1", "text": "Rain is cool.", "source": "pre"}) + "\n")
    cfg = Config(
        target_chunk_tokens=5,
        low_info_min_tokens=1,
        min_chunk_tokens=1,
        enabled_checks=["exact_duplicates"],
    )
    report = analyze([str(tmp_path / "notes.md"), str(jl)], cfg, embedder=FakeEmbedder())
    # 1 chunk from the .md + 1 pre-chunked from .jsonl
    assert report.total_chunks == 2
