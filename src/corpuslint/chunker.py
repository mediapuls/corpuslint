from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .models import Chunk, Document
from .tokenize import count_tokens


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def chunk_documents(docs: list[Document], config: Config) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc in docs:
        buf: list[str] = []
        buf_tokens = 0
        idx = 0
        for para in _paragraphs(doc.text):
            buf.append(para)
            buf_tokens += count_tokens(para)
            if buf_tokens >= config.target_chunk_tokens:
                text = "\n\n".join(buf)
                chunks.append(Chunk(id=f"{doc.source}#{idx}", text=text, source=doc.source, index=idx))
                idx += 1
                buf, buf_tokens = [], 0
        if buf:
            text = "\n\n".join(buf)
            chunks.append(Chunk(id=f"{doc.source}#{idx}", text=text, source=doc.source, index=idx))
    return chunks


def load_prechunked_jsonl(path: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        source = obj.get("source", path)
        chunks.append(
            Chunk(
                id=obj.get("id", f"{source}#{i}") if obj.get("source") else f"{path}#{i}",
                text=obj["text"],
                source=source,
                index=i,
                metadata=obj.get("metadata", {}),
            )
        )
    return chunks
