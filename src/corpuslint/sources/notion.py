from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import warnings
from typing import Callable
from urllib.parse import urlencode

from ..config import Config
from ..models import Document
from .base import SourceError, register

_API_BASE = "https://api.notion.com/v1"
# Required by Notion on every request; pinning it keeps the response shape stable.
_NOTION_VERSION = "2022-06-28"
# Notion's server-side ceiling for page_size on both endpoints is 100.
_PAGE_SIZE = 100
# Cap how deep we follow has_children so a self-referential / pathological tree
# can't recurse forever. Real Notion pages nest far shallower than this.
_MAX_BLOCK_DEPTH = 10

# Block types whose rich_text we turn into corpus text. Layout-only blocks
# (divider, image, table_of_contents, …) carry no prose and are ignored.
_TEXT_BLOCK_TYPES = {
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "quote",
    "callout",
    "code",
}


class NotionError(SourceError):
    """Raised when the Notion source cannot run (missing token/database, HTTP failure)."""


def _http_request_json(url: str, token: str, method: str = "GET", payload: dict | None = None) -> dict:
    """Call ``url`` with Bearer auth + the required version header, parse the JSON.

    Uses the standard library only, so Notion works with no optional extra.
    Errors are wrapped in NotionError; the token is never echoed back.
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise NotionError(
                f"Notion rejected the credentials (HTTP {e.code}) — check NOTION_TOKEN."
            ) from e
        if e.code == 404:
            raise NotionError(
                "Notion returned HTTP 404 — check the database_id and that the "
                "integration has been shared with that database."
            ) from e
        raise NotionError(f"Notion request failed with HTTP {e.code}.") from e
    except urllib.error.URLError as e:
        # e.reason may name the host but never carries the auth header/token.
        raise NotionError(f"could not reach Notion: {e.reason}") from e


def _rich_text_to_str(rich_text: list[dict] | None) -> str:
    """Concatenate the ``plain_text`` of a Notion rich_text array."""
    return "".join(rt.get("plain_text", "") for rt in (rich_text or []))


def _block_text(block: dict) -> str:
    """Text for a single block, or '' for a layout-only / unsupported block."""
    btype = block.get("type", "")
    if btype not in _TEXT_BLOCK_TYPES:
        return ""
    return _rich_text_to_str(block.get(btype, {}).get("rich_text", []))


def _query_database(
    database_id: str,
    fetch: Callable[[str | None], dict],
) -> list[dict]:
    """Follow the query cursor through every page in the database.

    ``fetch(start_cursor)`` returns one ``/databases/{id}/query`` response. The
    loop advances via ``next_cursor`` until ``has_more`` is false, so the whole
    database is pulled with no silent cap.
    """
    pages: list[dict] = []
    cursor: str | None = None
    while True:
        resp = fetch(cursor)
        pages.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
        if not cursor:
            break
    return pages


def _blocks_to_text(
    block_id: str,
    fetch_children: Callable[[str, str | None], dict],
    depth: int = 0,
) -> str:
    """Concatenate the text of ``block_id``'s children, recursing into nested blocks.

    ``fetch_children(block_id, start_cursor)`` returns one
    ``/blocks/{id}/children`` response. Children paginate via ``next_cursor``;
    blocks with ``has_children`` are recursed up to ``_MAX_BLOCK_DEPTH``.
    """
    if depth > _MAX_BLOCK_DEPTH:
        return ""
    lines: list[str] = []
    cursor: str | None = None
    while True:
        resp = fetch_children(block_id, cursor)
        for block in resp.get("results", []):
            text = _block_text(block)
            if text:
                lines.append(text)
            if block.get("has_children"):
                child = _blocks_to_text(block["id"], fetch_children, depth + 1)
                if child:
                    lines.append(child)
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
        if not cursor:
            break
    return "\n".join(lines)


def _page_title(page: dict) -> str:
    """Pull the page title from its ``title``-type property (any column name)."""
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return _rich_text_to_str(prop.get("title", []))
    return ""


def _page_source(page: dict) -> str:
    """Traceable source: the page's Notion URL, else a ``notion://<id>`` fallback."""
    return page.get("url") or f"notion://{page.get('id', '')}"


def _documents_from_database(
    database_id: str,
    query_fetch: Callable[[str | None], dict],
    children_fetch: Callable[[str, str | None], dict],
) -> list[Document]:
    """Map every page in the database to a Document (title + block text)."""
    docs: list[Document] = []
    for page in _query_database(database_id, query_fetch):
        page_id = str(page.get("id", ""))
        title = _page_title(page)
        body = _blocks_to_text(page_id, children_fetch)
        text = "\n".join(part for part in (title, body) if part.strip())
        if not text.strip():
            warnings.warn(
                f"skipping empty Notion page {page_id or '<no id>'} ({title!r})",
                UserWarning,
                stacklevel=2,
            )
            continue
        docs.append(Document(text=text, source=_page_source(page)))
    return docs


def _read_token() -> str:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise NotionError("NOTION_TOKEN is not set. Export it to use --source notion.")
    return token


def load_notion_documents(database_id: str, config: Config) -> list[Document]:
    token = _read_token()

    def query_fetch(cursor: str | None) -> dict:
        payload: dict = {"page_size": _PAGE_SIZE}
        if cursor:
            payload["start_cursor"] = cursor
        return _http_request_json(
            f"{_API_BASE}/databases/{database_id}/query", token, method="POST", payload=payload
        )

    def children_fetch(block_id: str, cursor: str | None) -> dict:
        params: dict = {"page_size": _PAGE_SIZE}
        if cursor:
            params["start_cursor"] = cursor
        url = f"{_API_BASE}/blocks/{block_id}/children?{urlencode(params)}"
        return _http_request_json(url, token, method="GET")

    docs = _documents_from_database(database_id, query_fetch, children_fetch)
    # Report the count on stderr (keeps stdout/--json clean) to back up the
    # "pages through everything, no silent cap" guarantee.
    print(f"fetched {len(docs)} documents from Notion database {database_id!r}", file=sys.stderr)
    return docs


class NotionSource:
    name = "notion"

    def load(self, config: Config) -> list[Document]:
        # database_id comes from source_options; the token comes from the
        # environment only (never CLI/config, so secrets don't land in files).
        database_id = config.source_options.get("database_id")
        if not database_id:
            raise SourceError(
                "the notion source requires a database "
                "(pass --source-opt database_id=<id>)"
            )
        return load_notion_documents(database_id, config)


register(NotionSource())
