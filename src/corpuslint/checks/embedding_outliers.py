from __future__ import annotations

import numpy as np

from ..models import Finding, Severity
from .base import CheckContext, register


class EmbeddingOutliersCheck:
    name = "embedding_outliers"

    def run(self, ctx: CheckContext) -> list[Finding]:
        if not ctx.embeddings or len(ctx.chunks) < 4:
            return []
        m = np.asarray(ctx.embeddings, dtype=float)
        centroid = m.mean(axis=0)
        distances = np.linalg.norm(m - centroid, axis=1)
        std = distances.std()
        if std == 0:
            return []
        zscores = (distances - distances.mean()) / std
        findings: list[Finding] = []
        for chunk, z in zip(ctx.chunks, zscores):
            if z > ctx.config.outlier_zscore:
                findings.append(
                    Finding(
                        check=self.name,
                        severity=Severity.INFO,
                        message=f"embedding outlier (z={z:.1f}) — possible junk or wrong language",
                        chunk_ids=(chunk.id,),
                        source=chunk.source,
                    )
                )
        return findings


register(EmbeddingOutliersCheck())
