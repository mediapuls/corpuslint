from __future__ import annotations

import sys
from pathlib import Path

import pytest

from corpuslint import mcp_server
from corpuslint.embedder import EmbedderUnavailable

_DUPLICATE_PARAGRAPH = (
    "Our refund policy allows customers to request a full refund within thirty days "
    "of purchase. Contact support with your order number and the reason for the return "
    "and we will process the refund to the original payment method within five business days."
)


def _corpus_with_duplicate(tmp_path: Path) -> str:
    (tmp_path / "a.md").write_text(_DUPLICATE_PARAGRAPH)
    (tmp_path / "b.md").write_text(_DUPLICATE_PARAGRAPH)
    return str(tmp_path)


# ---- tool function: happy path ----------------------------------------------


def test_lint_corpus_reports_exact_duplicate(tmp_path: Path):
    result = mcp_server.lint_corpus(_corpus_with_duplicate(tmp_path), embedder="none")

    assert result["score"] < 100
    assert result["total_chunks"] >= 2
    assert result["counts_by_check"].get("exact_duplicates", 0) >= 1
    # No warning when the caller explicitly asked for the dependency-free embedder.
    assert "warning" not in result


def test_lint_corpus_returns_agent_friendly_shape(tmp_path: Path):
    result = mcp_server.lint_corpus(_corpus_with_duplicate(tmp_path), embedder="none")

    assert set(result) >= {"score", "total_chunks", "counts_by_check", "top_offenders", "findings"}
    assert isinstance(result["score"], int)
    assert isinstance(result["counts_by_check"], dict)
    assert isinstance(result["findings"], list)
    finding = result["findings"][0]
    assert set(finding) >= {"check", "severity", "message", "chunk_ids", "source"}


def test_lint_corpus_none_embedder_is_dependency_free(tmp_path: Path):
    (tmp_path / "only.md").write_text(_DUPLICATE_PARAGRAPH)
    result = mcp_server.lint_corpus(str(tmp_path), embedder="none")
    assert "error" not in result
    assert result["total_chunks"] >= 1


def test_lint_corpus_fail_under_reports_pass_state(tmp_path: Path):
    result = mcp_server.lint_corpus(
        _corpus_with_duplicate(tmp_path), embedder="none", fail_under=100
    )
    # A corpus with a duplicate scores below 100, so it must not pass.
    assert result["passed"] is False


# ---- fallback: local embedder unavailable -----------------------------------


def test_local_falls_back_to_none_when_embedder_returns_none(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(mcp_server, "get_embedder", lambda name, cfg: None)
    result = mcp_server.lint_corpus(_corpus_with_duplicate(tmp_path), embedder="local")

    assert "warning" in result
    assert "corpuslint[local]" in result["warning"]
    # Analysis still ran; semantic-free checks still produce a score.
    assert result["score"] < 100
    assert result["counts_by_check"].get("exact_duplicates", 0) >= 1


def test_local_falls_back_when_embedder_raises_unavailable(tmp_path: Path, monkeypatch):
    def _raise(name, cfg):
        raise EmbedderUnavailable("no sentence-transformers")

    monkeypatch.setattr(mcp_server, "get_embedder", _raise)
    result = mcp_server.lint_corpus(_corpus_with_duplicate(tmp_path), embedder="local")

    assert "warning" in result
    assert "corpuslint[local]" in result["warning"]
    assert result["score"] < 100


# ---- bad input --------------------------------------------------------------


def test_missing_path_returns_clean_error(tmp_path: Path):
    result = mcp_server.lint_corpus(str(tmp_path / "does-not-exist"), embedder="none")
    assert "error" in result
    assert "score" not in result
    assert isinstance(result["error"], str)


# ---- server wiring ----------------------------------------------------------


def test_build_server_registers_the_tool():
    server = mcp_server._build_server()
    tool_names = {t.name for t in server._tool_manager.list_tools()}
    assert "lint_corpus" in tool_names


def test_main_without_mcp_installed_gives_actionable_error(monkeypatch):
    # Force `from mcp.server.fastmcp import FastMCP` to fail.
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    monkeypatch.setitem(sys.modules, "mcp.server", None)
    monkeypatch.setitem(sys.modules, "mcp", None)
    with pytest.raises(SystemExit) as exc:
        mcp_server.main()
    assert "corpuslint[mcp]" in str(exc.value)


# ---- base install isolation -------------------------------------------------


def test_importing_mcp_server_does_not_require_mcp(monkeypatch):
    """The heavy `mcp` import must be deferred so the base package imports fine."""
    monkeypatch.delitem(sys.modules, "corpuslint.mcp_server", raising=False)
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)

    import corpuslint.mcp_server as mod

    assert callable(mod.lint_corpus)
