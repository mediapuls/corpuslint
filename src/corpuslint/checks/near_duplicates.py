from __future__ import annotations

from ..models import Finding, Severity
from ..similarity import cosine_matrix
from .base import CheckContext, register


class NearDuplicatesCheck:
    name = "near_duplicates"

    def run(self, ctx: CheckContext) -> list[Finding]:
        if not ctx.embeddings:
            return []
        sims = cosine_matrix(ctx.embeddings)
        chunks = ctx.chunks
        threshold = ctx.config.near_dupe_threshold
        findings: list[Finding] = []
        n = len(chunks)
        for i in range(n):
            for j in range(i + 1, n):
                if chunks[i].text.strip() == chunks[j].text.strip():
                    continue  # exact dupes are another check's job
                if sims[i, j] >= threshold:
                    findings.append(
                        Finding(
                            check=self.name,
                            severity=Severity.WARNING,
                            message=f"near-duplicate (cosine {sims[i, j]:.2f}) crowds out relevant hits",
                            chunk_ids=(chunks[i].id, chunks[j].id),
                            source=chunks[i].source,
                        )
                    )
        return findings


register(NearDuplicatesCheck())
