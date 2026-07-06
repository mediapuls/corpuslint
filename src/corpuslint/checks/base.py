from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..config import Config
from ..models import Chunk, Finding


@dataclass
class CheckContext:
    chunks: list[Chunk]
    embeddings: list[list[float]] | None
    config: Config
    llm: object | None = None


class Check(Protocol):
    name: str

    def run(self, ctx: CheckContext) -> list[Finding]: ...


REGISTRY: dict[str, "Check"] = {}


def register(check: "Check") -> "Check":
    REGISTRY[check.name] = check
    return check


def get_enabled_checks(config: Config) -> list["Check"]:
    return [REGISTRY[name] for name in REGISTRY if name in config.enabled_checks]
