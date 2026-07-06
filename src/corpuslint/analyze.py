from __future__ import annotations

from . import checks as _checks  # noqa: F401  (registers all checks on import)
from .checks.base import CheckContext, get_enabled_checks
from .chunker import chunk_documents, load_prechunked_jsonl
from .config import Config
from .loader import load_documents
from .models import Chunk, Report
from .scoring import compute_score


def _collect_chunks(paths: list[str], config: Config) -> list[Chunk]:
    chunks: list[Chunk] = []
    docs_paths = [p for p in paths if not p.endswith(".jsonl")]
    for p in paths:
        if p.endswith(".jsonl"):
            chunks.extend(load_prechunked_jsonl(p))
    if docs_paths:
        chunks.extend(chunk_documents(load_documents(docs_paths, config), config))
    return chunks


def analyze(paths: list[str], config: Config, embedder=None, llm=None) -> Report:
    chunks = _collect_chunks(paths, config)
    embeddings = embedder.embed([c.text for c in chunks]) if embedder and chunks else None
    ctx = CheckContext(chunks=chunks, embeddings=embeddings, config=config, llm=llm)
    findings = []
    for check in get_enabled_checks(config):
        findings.extend(check.run(ctx))
    score = compute_score(findings, len(chunks), config)
    return Report(total_chunks=len(chunks), findings=findings, score=score)
