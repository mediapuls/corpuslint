from __future__ import annotations

from ..models import Finding, Severity
from ..tokenize import count_tokens
from .base import CheckContext, register


def _alnum_ratio(text: str) -> float:
    stripped = text.replace(" ", "")
    if not stripped:
        return 0.0
    return sum(ch.isalnum() for ch in stripped) / len(stripped)


class LowInformationCheck:
    name = "low_information"

    def run(self, ctx: CheckContext) -> list[Finding]:
        findings: list[Finding] = []
        for c in ctx.chunks:
            if count_tokens(c.text) < ctx.config.low_info_min_tokens or _alnum_ratio(c.text) < 0.5:
                findings.append(
                    Finding(
                        check=self.name,
                        severity=Severity.WARNING,
                        message="low-information chunk (too short or mostly symbols)",
                        chunk_ids=(c.id,),
                        source=c.source,
                    )
                )
        return findings


register(LowInformationCheck())
