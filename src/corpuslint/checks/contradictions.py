from __future__ import annotations

from ..models import Finding, Severity
from ..similarity import cosine_matrix
from .base import CheckContext, register

_PROMPT_HEADER = (
    "You are checking a knowledge base for contradictions.\n"
    "Do these two passages state facts that directly contradict each other?\n"
    "Answer with exactly YES or NO."
)


class ContradictionsCheck:
    name = "contradictions"

    def run(self, ctx: CheckContext) -> list[Finding]:
        if not ctx.config.use_llm or ctx.llm is None or not ctx.embeddings:
            return []
        sims = cosine_matrix(ctx.embeddings)
        related = ctx.config.near_dupe_threshold - 0.15
        chunks = ctx.chunks
        findings: list[Finding] = []
        n = len(chunks)
        for i in range(n):
            for j in range(i + 1, n):
                if sims[i, j] < related:
                    continue
                prompt = (
                    f"{_PROMPT_HEADER}\n\nPassage A:\n{chunks[i].text}\n\nPassage B:\n{chunks[j].text}\n"
                )
                answer = ctx.llm.complete(prompt)
                if answer.strip().upper().startswith("YES"):
                    findings.append(
                        Finding(
                            check=self.name,
                            severity=Severity.ERROR,
                            message="passages appear to contradict each other",
                            chunk_ids=(chunks[i].id, chunks[j].id),
                            source=chunks[i].source,
                        )
                    )
        return findings


register(ContradictionsCheck())
