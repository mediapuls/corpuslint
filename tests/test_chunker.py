from pathlib import Path

from corpuslint.config import Config
from corpuslint.models import Document
from corpuslint.chunker import chunk_documents, load_prechunked_jsonl


def test_chunk_documents_groups_paragraphs_to_target():
    doc = Document(text="para one.\n\npara two.\n\npara three.", source="d.md")
    chunks = chunk_documents([doc], Config(target_chunk_tokens=3))
    assert all(c.source == "d.md" for c in chunks)
    assert chunks[0].id == "d.md#0"
    assert len(chunks) >= 2


def test_load_prechunked_jsonl_fills_missing_fields(tmp_path: Path):
    p = tmp_path / "c.jsonl"
    p.write_text('{"text": "hello"}\n{"text": "world", "source": "s"}\n')
    chunks = load_prechunked_jsonl(str(p))
    assert chunks[0].id == f"{p}#0"
    assert chunks[1].source == "s"
