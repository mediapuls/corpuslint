from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
import warnings
from typing import Callable
from urllib.parse import urlencode

from ..config import Config
from ..loader import html_to_text
from ..models import Document
from .base import SourceError, register

# How many pages to pull per request. Confluence's server-side ceiling for this
# endpoint is 100; 50 is a safe, well-behaved default that still pages quickly.
_PAGE_LIMIT = 50


class ConfluenceError(SourceError):
    """Raised when the Confluence source cannot run (missing space/creds, HTTP failure)."""


def _build_content_url(base_url: str, space: str, start: int, limit: int) -> str:
    """URL for one page of the current pages in ``space`` with their storage body."""
    query = urlencode(
        {
            "spaceKey": space,
            "type": "page",
            "status": "current",
            "expand": "body.storage",
            "limit": limit,
            "start": start,
        }
    )
    return f"{base_url}/wiki/rest/api/content?{query}"


def _http_get_json(url: str, email: str, api_token: str) -> dict:
    """GET ``url`` with HTTP Basic auth and parse the JSON body.

    Uses the standard library only, so Confluence works with no optional extra.
    Errors are wrapped in ConfluenceError; the token is never echoed back.
    """
    token = base64.b64encode(f"{email}:{api_token}".encode()).decode("ascii")
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ConfluenceError(
                "Confluence rejected the credentials (HTTP "
                f"{e.code}) — check CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN."
            ) from e
        raise ConfluenceError(f"Confluence request failed with HTTP {e.code}.") from e
    except urllib.error.URLError as e:
        # e.reason may name the host but never carries the auth header/token.
        raise ConfluenceError(f"could not reach Confluence: {e.reason}") from e


def _page_url(base_url: str, space: str, page_id: str) -> str:
    return f"{base_url.rstrip('/')}/wiki/spaces/{space}/pages/{page_id}"


def _storage_body(page: dict) -> str:
    return page.get("body", {}).get("storage", {}).get("value", "") or ""


def _documents_from_source(
    base_url: str,
    space: str,
    fetch: Callable[[int, int], dict],
    limit: int = _PAGE_LIMIT,
) -> list[Document]:
    """Page through every current page in ``space`` and map each to a Document.

    ``fetch(start, limit)`` returns one parsed ``/content`` response. The loop
    advances ``start`` by ``limit`` until a short (or empty) window arrives, so
    the whole space is pulled with no silent cap.
    """
    base_url = base_url.rstrip("/")
    docs: list[Document] = []
    start = 0
    while True:
        batch = fetch(start, limit).get("results", [])
        for page in batch:
            body = _storage_body(page)
            page_id = str(page.get("id", ""))
            title = page.get("title", "")
            if not body.strip():
                warnings.warn(
                    f"skipping Confluence page {page_id or '<no id>'} "
                    f"({title!r}) with empty body",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            text = html_to_text(f"<h1>{title}</h1>\n{body}")
            docs.append(Document(text=text, source=_page_url(base_url, space, page_id)))
        if len(batch) < limit:
            break
        start += limit
    return docs


def _read_credentials() -> tuple[str, str]:
    email = os.environ.get("CONFLUENCE_EMAIL")
    api_token = os.environ.get("CONFLUENCE_API_TOKEN")
    if not email:
        raise ConfluenceError(
            "CONFLUENCE_EMAIL is not set. Export it to use --source confluence."
        )
    if not api_token:
        raise ConfluenceError(
            "CONFLUENCE_API_TOKEN is not set. Export it to use --source confluence."
        )
    return email, api_token


def load_confluence_documents(base_url: str, space: str, config: Config) -> list[Document]:
    email, api_token = _read_credentials()
    base_url = base_url.rstrip("/")

    def fetch(start: int, limit: int) -> dict:
        return _http_get_json(_build_content_url(base_url, space, start, limit), email, api_token)

    docs = _documents_from_source(base_url, space, fetch, limit=_PAGE_LIMIT)
    # Report the count on stderr (keeps stdout/--json clean) to back up the
    # "pages through everything, no silent cap" guarantee.
    print(f"fetched {len(docs)} documents from Confluence space {space!r}", file=sys.stderr)
    return docs


class ConfluenceSource:
    name = "confluence"

    def load(self, config: Config) -> list[Document]:
        # Space selection comes from source_options; credentials come from the
        # environment only (never CLI/config, so secrets don't land in files).
        opts = config.source_options
        space = opts.get("space")
        if not space:
            raise SourceError(
                "the confluence source requires a space "
                "(pass --source-opt space=<KEY>, e.g. --source-opt space=NCPCS)"
            )
        base_url = opts.get("base_url") or os.environ.get("CONFLUENCE_BASE_URL")
        if not base_url:
            raise SourceError(
                "the confluence source requires a base URL "
                "(pass --source-opt base_url=https://<site>.atlassian.net "
                "or set the CONFLUENCE_BASE_URL env var)"
            )
        return load_confluence_documents(base_url, space, config)


register(ConfluenceSource())
