import json
from pathlib import Path

import pytest

from corpuslint.analyze import analyze
from corpuslint.config import Config
from corpuslint.loader import load_documents
from corpuslint.sources.files import FilesSource
from tests.conftest import FakeEmbedder


def test_files_source_reads_config_paths_returns_same_documents(tmp_path: Path):
    (tmp_path / "a.md").write_text("Cats are great pets.")
    (tmp_path / "b.md").write_text("Dogs are loyal companions.")
    cfg = Config(paths=[str(tmp_path)])

    got = FilesSource().load(cfg)
    expected = load_documents([str(tmp_path)], cfg)

    assert got == expected


def test_files_source_findings_parity_with_paths_route(tmp_path: Path):
    """Loading via FilesSource + analyze(documents=) yields the same findings as
    the legacy analyze(paths=) route on the same fixture."""
    (tmp_path / "a.md").write_text("Cats are great pets.")
    (tmp_path / "b.md").write_text("Cats are great pets.")
    cfg = Config(
        target_chunk_tokens=2,
        low_info_min_tokens=1,
        min_chunk_tokens=1,
        enabled_checks=["exact_duplicates"],
        paths=[str(tmp_path)],
    )

    documents = FilesSource().load(cfg)
    via_source = analyze([], cfg, embedder=FakeEmbedder(), documents=documents)
    via_paths = analyze([str(tmp_path)], cfg, embedder=FakeEmbedder())

    assert via_source.findings_by_check().keys() == via_paths.findings_by_check().keys()
    assert via_source.score == via_paths.score


def test_files_source_skips_jsonl_paths_with_warning(tmp_path: Path):
    """FilesSource.load() is the document-loading path; .jsonl files are pre-chunked
    data that bypass it entirely (the CLI fast-path handles them in analyze()).
    Confirm FilesSource.load() skips .jsonl AND warns, so a library caller doing
    get_source("files").load(cfg) directly isn't silently missing that data.

    This is intentional and documented in files.py — this test pins that contract.
    """
    (tmp_path / "a.md").write_text("Cats are great pets.")
    jl = tmp_path / "pre.jsonl"
    jl.write_text(json.dumps({"id": "p1", "text": "Dogs are loyal.", "source": "pre"}) + "\n")
    cfg = Config(paths=[str(tmp_path / "a.md"), str(jl)])

    with pytest.warns(UserWarning, match="jsonl"):
        docs = FilesSource().load(cfg)
    sources = [d.source for d in docs]
    # Only the .md was loaded; the .jsonl was skipped
    assert len(docs) == 1
    assert str(tmp_path / "a.md") in sources
    assert str(jl) not in sources
