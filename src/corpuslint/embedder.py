from __future__ import annotations

from typing import Protocol, runtime_checkable

from .config import Config


class EmbedderUnavailable(RuntimeError):
    pass


@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise EmbedderUnavailable(
                "Local embeddings need the optional extra: pip install 'corpuslint[local]'"
            ) from e
        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.encode(texts, convert_to_numpy=True)]


def get_embedder(name: str, config: Config) -> Embedder | None:
    if name == "none":
        return None
    if name == "local":
        try:
            return LocalEmbedder()
        except EmbedderUnavailable:
            return None
    raise ValueError(f"unknown embedder: {name}")
