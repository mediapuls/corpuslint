from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
import warnings
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from ..config import Config
from ..loader import HTML_EXTS, TEXT_EXTS, load_documents
from ..models import Document
from .base import SourceError, register

# Microsoft Graph v1.0 root. All site/drive/item calls hang off this.
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# App-only (client-credentials) token endpoint, per tenant.
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
# The app-only scope for Graph; combined with the app's granted Application
# permissions (Sites.Read.All) this returns a token good for reading drives.
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Drive items with these extensions are downloaded and handed to the existing
# file loader/parsers. Derived from the loader's own supported sets so the two
# stay in lockstep: if the loader learns a new format, SharePoint picks it up for
# free. Everything else (images, PDFs, Office binaries, archives) is skipped
# without a download — the loader has no parser for them.
_SUPPORTED_EXTS = TEXT_EXTS | HTML_EXTS

# Ceiling on folder-tree recursion. A misconfigured or maliciously self-referential
# listing would otherwise recurse until the stack blows; SharePoint libraries this
# deep are not real, so bounding here is safe and keeps traversal terminating.
_MAX_DEPTH = 64


class SharePointError(SourceError):
    """Raised when the SharePoint source cannot run (missing creds/site, HTTP failure)."""


def _read_credentials() -> tuple[str, str, str]:
    """The three app-registration credentials, read from the environment only.

    Uses the standard Microsoft variable names so nothing sensitive lands in
    ``--source-opt`` or ``.corpuslint.yml``. A missing variable is reported by
    name so the user knows exactly what to export.
    """
    tenant_id = os.environ.get("AZURE_TENANT_ID")
    client_id = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")
    if not tenant_id:
        raise SharePointError(
            "AZURE_TENANT_ID is not set. Export it to use --source sharepoint."
        )
    if not client_id:
        raise SharePointError(
            "AZURE_CLIENT_ID is not set. Export it to use --source sharepoint."
        )
    if not client_secret:
        raise SharePointError(
            "AZURE_CLIENT_SECRET is not set. Export it to use --source sharepoint."
        )
    return tenant_id, client_id, client_secret


def _get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Fetch an app-only Graph access token via the client-credentials flow.

    Stdlib only, so SharePoint needs no optional extra. Failures are wrapped in
    :class:`SharePointError`; neither the secret nor any error body is echoed —
    only the HTTP status — so a misconfigured secret can't leak through a log.
    """
    url = _TOKEN_URL.format(tenant=tenant_id)
    body = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": _GRAPH_SCOPE,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Never surface e.read() — the error body can echo the submitted secret.
        raise SharePointError(
            f"Microsoft rejected the app credentials (HTTP {e.code}) — check "
            "AZURE_TENANT_ID, AZURE_CLIENT_ID and AZURE_CLIENT_SECRET."
        ) from e
    except urllib.error.URLError as e:
        # e.reason may name the host but never carries the request body/secret.
        raise SharePointError(
            f"could not reach the Microsoft login endpoint: {e.reason}"
        ) from e
    token = payload.get("access_token")
    if not token:
        raise SharePointError("Microsoft token response contained no access_token.")
    return token


def _graph_get(url: str, token: str) -> bytes:
    """GET ``url`` from Graph with a Bearer token, returning the raw body.

    Used both for JSON listings (via :func:`_graph_get_json`) and for file
    ``/content`` downloads. Errors are wrapped in :class:`SharePointError`; the
    token is never echoed back.
    """
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SharePointError(
                f"Microsoft Graph denied access (HTTP {e.code}) — check the app's "
                "Sites.Read.All Application permission and that admin consent was granted."
            ) from e
        raise SharePointError(f"Microsoft Graph request failed with HTTP {e.code}.") from e
    except urllib.error.URLError as e:
        raise SharePointError(f"could not reach Microsoft Graph: {e.reason}") from e


def _graph_get_json(url: str, token: str) -> dict:
    """GET ``url`` from Graph and parse the JSON body."""
    return json.loads(_graph_get(url, token).decode("utf-8"))


def _resolve_site_id(site: str, token: str) -> str:
    """Resolve a ``hostname:/sites/path`` site descriptor to its Graph site id.

    ``site`` is addressed with Graph's colon path syntax
    (``/sites/{hostname}:/sites/{path}``). Returns the opaque site id used for
    every subsequent drive call.
    """
    data = _graph_get_json(f"{GRAPH_BASE}/sites/{site}", token)
    site_id = data.get("id")
    if not site_id:
        raise SharePointError(
            f"could not resolve SharePoint site {site!r} — check the "
            "site=<hostname>:/sites/<path> value."
        )
    return site_id


def _drive_base(site_id: str, drive_id: str | None) -> str:
    """The Graph URL prefix for the target drive.

    An explicit ``drive_id`` addresses that library directly; otherwise the
    site's default document library (``.../drive``) is used.
    """
    if drive_id:
        return f"{GRAPH_BASE}/drives/{drive_id}"
    return f"{GRAPH_BASE}/sites/{site_id}/drive"


def _start_url(drive_base: str, folder: str | None) -> str:
    """The listing URL the traversal starts from.

    With no ``folder`` this is the drive root's children; a ``folder`` scopes the
    walk to that path via Graph's ``root:/path:/children`` colon syntax.
    """
    if folder:
        return f"{drive_base}/root:/{folder.strip('/')}:/children"
    return f"{drive_base}/root/children"


def _iter_children(list_url: str, get_json: Callable[[str], dict]):
    """Yield every child item under ``list_url``, following ``@odata.nextLink``.

    Graph pages large folders; each response carries a ``value`` array and, when
    more remain, an absolute ``@odata.nextLink``. Following it to exhaustion means
    the whole folder is enumerated with no silent cap.
    """
    url: str | None = list_url
    while url:
        data = get_json(url)
        yield from data.get("value", [])
        url = data.get("@odata.nextLink")


def _parse_item_bytes(body: bytes, ext: str, config: Config) -> Document | None:
    """Parse downloaded file bytes via the shared loader.

    Writes the bytes to a temp file carrying the item's extension so the loader
    dispatches to the right parser (md/txt → text, html/htm → the HTML
    extractor), then returns the parsed document (source rebranded by the caller).
    The temp file is always cleaned up, even if parsing raises.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        tmp.write(body)
        tmp.close()
        docs = load_documents([tmp.name], config)
    finally:
        if not tmp.closed:
            tmp.close()  # a failed write leaves the handle open; don't leak it
        os.unlink(tmp.name)
    return docs[0] if docs else None


def _item_source(item: dict) -> str:
    """A traceable source for a drive item: its webUrl, else a sharepoint:// id."""
    return item.get("webUrl") or f"sharepoint://{item.get('id', '')}"


def _documents_from_drive(
    drive_base: str,
    start_url: str,
    get_json: Callable[[str], dict],
    download: Callable[[str], bytes],
    config: Config,
    max_depth: int = _MAX_DEPTH,
) -> list[Document]:
    """Walk the drive from ``start_url``, parsing every supported file into a Document.

    Folders are recursed into (bounded by ``max_depth``); files whose extension
    the loader can't parse are skipped before any download. A per-file download or
    parse failure is warned about (by name only — never the raw error text, which
    could carry backend detail) and the walk continues.
    """
    docs: list[Document] = []

    def walk(list_url: str, depth: int) -> None:
        if depth > max_depth:
            return
        for item in _iter_children(list_url, get_json):
            if "folder" in item:
                walk(f"{drive_base}/items/{item['id']}/children", depth + 1)
            elif "file" in item:
                _collect_file(item, download, config, docs)

    walk(start_url, 0)
    return docs


def _collect_file(
    item: dict, download: Callable[[str], bytes], config: Config, docs: list[Document]
) -> None:
    """Download and parse one file item, appending a Document (or skip-with-warning)."""
    name = item.get("name", "")
    if Path(name).suffix.lower() not in _SUPPORTED_EXTS:
        return
    try:
        body = download(item["id"])
        doc = _parse_item_bytes(body, Path(name).suffix.lower(), config)
    except Exception as e:  # noqa: BLE001 - one bad file must not abort the run
        warnings.warn(
            f"skipping {name or item.get('id', '<no id>')}: {type(e).__name__}",
            UserWarning,
            stacklevel=2,
        )
        return
    if doc is not None:
        docs.append(Document(text=doc.text, source=_item_source(item)))


def load_sharepoint_documents(config: Config) -> list[Document]:
    """Pull every supported file from a SharePoint/OneDrive drive into Documents.

    Credentials come from the environment (``AZURE_TENANT_ID`` /
    ``AZURE_CLIENT_ID`` / ``AZURE_CLIENT_SECRET``); the site, optional drive and
    optional folder come from ``source_options``. Acquires an app-only token,
    resolves the site (unless ``site_id`` was given), then walks the drive.
    """
    tenant_id, client_id, client_secret = _read_credentials()
    token = _get_access_token(tenant_id, client_id, client_secret)

    opts = config.source_options
    site_id = opts.get("site_id") or _resolve_site_id(opts["site"], token)
    drive_base = _drive_base(site_id, opts.get("drive_id"))
    start_url = _start_url(drive_base, opts.get("folder"))

    def get_json(url: str) -> dict:
        return _graph_get_json(url, token)

    def download(item_id: str) -> bytes:
        return _graph_get(f"{drive_base}/items/{item_id}/content", token)

    docs = _documents_from_drive(drive_base, start_url, get_json, download, config)
    # Report on stderr (keeps stdout/--json clean) to back the "no silent cap" claim.
    print(f"fetched {len(docs)} documents from SharePoint", file=sys.stderr)
    return docs


class SharePointSource:
    name = "sharepoint"

    def load(self, config: Config) -> list[Document]:
        # Site selection comes from source_options; credentials come from the
        # environment only (never CLI/config, so secrets don't land in files).
        opts = config.source_options
        if not opts.get("site") and not opts.get("site_id"):
            raise SourceError(
                "the sharepoint source requires a site "
                "(pass --source-opt site=<hostname>:/sites/<path>, "
                "e.g. --source-opt site=contoso.sharepoint.com:/sites/Engineering, "
                "or --source-opt site_id=<id>)"
            )
        return load_sharepoint_documents(config)


register(SharePointSource())
