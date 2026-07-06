from __future__ import annotations

from ..models import Finding, Severity
from ..tokenize import count_tokens
from .base import CheckContext, register


class ChunkSizeCheck:
    name = "chunk_size"

    def run(self, ctx: CheckContext) -> list[Finding]:
        findings: list[Finding] = []
        for c in ctx.chunks:
            tokens = count_tokens(c.text)
            if tokens < ctx.config.min_chunk_tokens:
                msg = f"undersized chunk ({tokens} tokens) — retrieval too granular"
            elif tokens > ctx.config.max_chunk_tokens:
                msg = f"oversized chunk ({tokens} tokens) — retrieval imprecise"
            else:
                continue
            findings.append(
                Finding(self.name, Severity.WARNING, msg, (c.id,), c.source)
            )
        return findings


register(ChunkSizeCheck())
