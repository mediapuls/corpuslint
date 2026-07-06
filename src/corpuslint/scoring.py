from __future__ import annotations

from .config import Config
from .models import Finding


def compute_score(findings: list[Finding], total_chunks: int, config: Config) -> int:
    if total_chunks == 0:
        return 100
    penalty = sum(config.weights.get(f.check, 0.5) for f in findings) / total_chunks * 100
    return int(max(0.0, min(100.0, 100.0 - penalty)))
