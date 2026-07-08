from __future__ import annotations

from typing import Protocol

from ..config import Config
from ..models import Document


class SourceError(RuntimeError):
    """Raised when a source cannot run (bad/missing options, backend failure)."""


class UnknownSourceError(SourceError):
    """Raised when an unregistered source name is requested."""


class Source(Protocol):
    name: str

    def load(self, config: Config) -> list[Document]: ...


REGISTRY: dict[str, "Source"] = {}


def register(source: "Source") -> "Source":
    REGISTRY[source.name] = source
    return source


def get_source(name: str) -> "Source":
    try:
        return REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(REGISTRY))
        raise UnknownSourceError(
            f"unknown source {name!r} (valid sources: {valid})"
        ) from None
