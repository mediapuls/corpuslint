from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Document:
    text: str
    source: str


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    source: str
    index: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    message: str
    chunk_ids: tuple[str, ...]
    source: str = ""


@dataclass
class Report:
    total_chunks: int
    findings: list[Finding]
    score: int

    def findings_by_check(self) -> dict[str, list[Finding]]:
        grouped: dict[str, list[Finding]] = defaultdict(list)
        for f in self.findings:
            grouped[f.check].append(f)
        return dict(grouped)

    def top_offenders(self, n: int = 5) -> list[tuple[str, int]]:
        counts = Counter(f.source for f in self.findings if f.source)
        return counts.most_common(n)
