from __future__ import annotations

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
        paths = [p for p in config.paths if not p.endswith(_PRECHUNKED_SUFFIX)]
        return load_documents(paths, config)


register(FilesSource())
