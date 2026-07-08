from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from .config import Config
from .models import Document

TEXT_EXTS = {".txt", ".md"}
HTML_EXTS = {".html", ".htm"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def html_to_text(html: str) -> str:
    """Strip tags/markup from an HTML string, returning collapsed plain text.

    Shared by the files loader (``.html`` inputs) and remote sources that hand
    back HTML/XHTML bodies (e.g. Confluence storage-format pages).
    """
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def _iter_files(paths: list[str]):
    for p in paths:
        path = Path(p)
        if path.is_dir():
            yield from sorted(path.rglob("*"))
        elif path.is_file():
            yield path


def load_documents(paths: list[str], config: Config) -> list[Document]:
    docs: list[Document] = []
    for file in _iter_files(paths):
        if not file.is_file():
            continue
        ext = file.suffix.lower()
        if ext in TEXT_EXTS:
            docs.append(Document(text=file.read_text(encoding="utf-8", errors="ignore"), source=str(file)))
        elif ext in HTML_EXTS:
            text = html_to_text(file.read_text(encoding="utf-8", errors="ignore"))
            docs.append(Document(text=text, source=str(file)))
    return docs
