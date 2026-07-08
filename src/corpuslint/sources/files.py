from __future__ import annotations

import warnings

from ..config import Config
from ..loader import load_documents
from ..models import Document
from .base import register

# ``.jsonl`` inputs are pre-chunked data, not documents to (re)chunk. The CLI's
# files flow routes them straight through the pipeline via load_prechunked_jsonl,
# so this document loader ignores them.
_PRECHUNKED_SUFFIX = ".jsonl"


class FilesSource:
    name = "files"

    def load(self, config: Config) -> list[Document]:
        """Load Documents from ``config.paths``.

        Note: pre-chunked ``.jsonl`` inputs are NOT returned here — they are
        already chunks, not documents, and are ingested separately by the CLI's
        files flow (``analyze(paths=...)`` → ``load_prechunked_jsonl``). Any
        ``.jsonl`` paths passed here are skipped with a ``UserWarning`` so a
        library caller doing ``get_source("files").load(cfg)`` directly isn't
        silently missing that data.
        """
        paths = [p for p in config.paths if not p.endswith(_PRECHUNKED_SUFFIX)]
        skipped = len(config.paths) - len(paths)
        if skipped:
            warnings.warn(
                f"FilesSource.load skipped {skipped} pre-chunked .jsonl path(s): "
                "these are ingested through the CLI/analyze() path, not the files "
                "document loader.",
                UserWarning,
                stacklevel=2,
            )
        return load_documents(paths, config)


register(FilesSource())
