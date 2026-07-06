from __future__ import annotations

from collections import defaultdict

from ..models import Finding, Severity
from .base import CheckContext, register


class ExactDuplicatesCheck:
    name = "exact_duplicates"

    def run(self, ctx: CheckContext) -> list[Finding]:
        groups: dict[str, list] = defaultdict(list)
        for c in ctx.chunks:
            groups[c.text.strip()].append(c)
        findings: list[Finding] = []
        for text, members in groups.items():
            if len(members) > 1:
                findings.append(
                    Finding(
                        check=self.name,
                        severity=Severity.ERROR,
                        message=f"{len(members)} identical chunks waste retrieval slots",
                        chunk_ids=tuple(m.id for m in members),
                        source=members[0].source,
                    )
                )
        return findings


register(ExactDuplicatesCheck())
