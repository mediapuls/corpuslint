"""Model Context Protocol server exposing corpuslint over stdio.

A thin wrapper so an AI agent (Claude Desktop, etc.) can lint a RAG corpus and
get back the Quality Score plus findings. The heavy `mcp` SDK is imported lazily
in `_build_server()` so the rest of the package installs and imports without it.
"""

from __future__ import annotations

import json
import os

from .analyze import analyze
from .config import load_config
from .embedder import get_embedder
from .report import render_json

_VALID_EMBEDDERS = ("local", "none")
_MCP_INSTALL_HINT = 'The corpuslint MCP server needs the optional extra: pip install "corpuslint[mcp]"'
_LOCAL_WARNING = (
    "The 'local' embedder is unavailable (sentence-transformers not installed), so "
    "semantic checks (near-duplicates, embedding outliers) were skipped. Install it "
    'with: pip install "corpuslint[local]"'
)


def lint_corpus(path: str, embedder: str = "local", fail_under: int | None = None) -> dict:
    """Lint a RAG corpus and return its Quality Score plus findings.

    Args:
        path: File or directory to check, or a `.jsonl` file of pre-chunked data.
        embedder: "local" (semantic checks via sentence-transformers) or "none"
            (dependency-free; skips near-duplicate and outlier checks).
        fail_under: Optional score threshold; when set, the result includes a
            boolean `passed` (score >= fail_under).

    Returns a JSON-serializable dict with `score`, `total_chunks`,
    `counts_by_check`, `top_offenders`, and `findings`. On an unreadable path it
    returns `{"error": "..."}` instead. If "local" is requested but unavailable,
    it falls back to "none" and adds a `warning` explaining how to enable it.
    """
    if embedder not in _VALID_EMBEDDERS:
        return {"error": f"unknown embedder {embedder!r}, use 'local' or 'none'"}
    if not os.path.exists(path):
        return {"error": f"path not found: {path!r}"}
    if not os.access(path, os.R_OK):
        return {"error": f"path is not readable: {path!r}"}

    cfg = load_config(None)
    if fail_under is not None:
        cfg.fail_under = fail_under

    # get_embedder returns None for "local" when sentence-transformers is missing;
    # fall back to no embeddings (semantic checks skipped) with a warning.
    emb = get_embedder(embedder, cfg)
    warning = _LOCAL_WARNING if embedder == "local" and emb is None else None

    try:
        report = analyze([path], cfg, embedder=emb)
    except Exception as e:  # noqa: BLE001 - surface a clean message, never a traceback
        return {"error": f"failed to analyze {path!r}: {e}"}

    payload = json.loads(render_json(report))
    result: dict = {
        "score": report.score,
        "total_chunks": report.total_chunks,
        "counts_by_check": {
            check: len(findings) for check, findings in report.findings_by_check().items()
        },
        "top_offenders": payload["top_offenders"],
        "findings": payload["findings"],
    }
    if fail_under is not None:
        result["passed"] = report.score >= fail_under
    if warning:
        result["warning"] = warning
    return result


def _build_server():
    """Construct the FastMCP server with the lint tool registered.

    Imports the MCP SDK lazily so importing this module (and the rest of the
    package) never requires the optional `mcp` extra.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:
        raise SystemExit(_MCP_INSTALL_HINT) from e

    server = FastMCP("corpuslint")
    server.tool()(lint_corpus)
    return server


def main() -> None:
    """Entry point for the `corpuslint-mcp` console script (stdio transport)."""
    _build_server().run()


if __name__ == "__main__":
    main()
