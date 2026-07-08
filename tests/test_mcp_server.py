from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from corpuslint import mcp_server

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


# ---- bad input --------------------------------------------------------------


def test_invalid_embedder_returns_clean_error(tmp_path: Path):
    result = mcp_server.lint_corpus(_corpus_with_duplicate(tmp_path), embedder="bogus")
    assert "error" in result
    assert "score" not in result
    assert "bogus" in result["error"]


def test_missing_path_returns_clean_error(tmp_path: Path):
    result = mcp_server.lint_corpus(str(tmp_path / "does-not-exist"), embedder="none")
    assert "error" in result
    assert "score" not in result
    assert isinstance(result["error"], str)


@pytest.mark.skipif(os.getuid() == 0, reason="chmod has no effect when running as root")
def test_unreadable_path_returns_clean_error(tmp_path: Path):
    unreadable = tmp_path / "secret.md"
    unreadable.write_text("content")
    unreadable.chmod(0o000)
    try:
        result = mcp_server.lint_corpus(str(unreadable), embedder="none")
        assert "error" in result
        assert "score" not in result
        assert isinstance(result["error"], str)
    finally:
        unreadable.chmod(0o644)  # restore so tmp_path cleanup succeeds


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


# ---- end-to-end MCP roundtrip (in-memory transport) ------------------------


@pytest.mark.anyio
async def test_e2e_mcp_roundtrip_list_and_call(tmp_path: Path):
    """Full stdio-equivalent roundtrip: connect an in-memory client to the real
    FastMCP server, list tools, then call lint_corpus on a fixture with a
    duplicate and verify the response shape and finding."""
    from mcp.shared.memory import create_connected_server_and_client_session

    (tmp_path / "a.md").write_text(_DUPLICATE_PARAGRAPH)
    (tmp_path / "b.md").write_text(_DUPLICATE_PARAGRAPH)

    server = mcp_server._build_server()
    async with create_connected_server_and_client_session(server) as session:
        tools_result = await session.list_tools()
        tool_names = {t.name for t in tools_result.tools}
        assert "lint_corpus" in tool_names

        call_result = await session.call_tool(
            "lint_corpus", {"path": str(tmp_path), "embedder": "none"}
        )
        assert not call_result.isError
        assert call_result.content, "expected at least one content block"

        payload = json.loads(call_result.content[0].text)  # type: ignore[attr-defined]
        assert set(payload) >= {"score", "total_chunks", "counts_by_check", "top_offenders", "findings"}
        assert payload["counts_by_check"].get("exact_duplicates", 0) >= 1
        assert payload["score"] < 100
