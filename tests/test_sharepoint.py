import io
import json
import urllib.error
import urllib.request
from urllib.parse import parse_qs

import pytest

from corpuslint.config import Config
from corpuslint.models import Document
from corpuslint.sources.base import SourceError, get_source
from corpuslint.sources.sharepoint import (
    SharePointError,
    SharePointSource,
    _documents_from_drive,
    _drive_base,
    _get_access_token,
    _graph_get,
    _graph_get_json,
    _read_credentials,
    _resolve_site_id,
    _start_url,
    load_sharepoint_documents,
)


def _cfg(**over) -> Config:
    cfg = Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def _set_creds(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-1")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-1")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "app-secret")


# ---- fakes -----------------------------------------------------------------


def _file_item(name: str, item_id: str, web_url: str | None = None) -> dict:
    item = {"id": item_id, "name": name, "file": {"mimeType": "text/plain"}}
    if web_url is not None:
        item["webUrl"] = web_url
    return item


def _folder_item(name: str, item_id: str) -> dict:
    return {"id": item_id, "name": name, "folder": {"childCount": 1}}


class _FakeDrive:
    """Serves paginated /children listings and /content downloads from a tree.

    ``tree`` maps a listing URL to a flat list of driveItems. Listings are split
    into pages of ``page_size`` and stitched together with ``@odata.nextLink`` so
    the connector's pagination loop is exercised for real. ``bodies`` maps an
    item id to its downloaded bytes. Every listing URL requested is recorded so a
    test can assert which folders were recursed into.
    """

    def __init__(self, tree: dict[str, list[dict]], bodies: dict[str, bytes], page_size: int = 100):
        self.tree = tree
        self.bodies = bodies
        self.page_size = page_size
        self.listed_urls: list[str] = []
        self.downloaded: list[str] = []

    def get_json(self, url: str) -> dict:
        # Strip a paging marker of the form "<base>#<offset>" the fake adds itself.
        base, _, marker = url.partition("#")
        self.listed_urls.append(base)
        items = self.tree.get(base, [])
        start = int(marker) if marker else 0
        window = items[start : start + self.page_size]
        nxt = start + self.page_size
        out: dict = {"value": window}
        if nxt < len(items):
            out["@odata.nextLink"] = f"{base}#{nxt}"
        return out

    def download(self, item_id: str) -> bytes:
        self.downloaded.append(item_id)
        return self.bodies[item_id]


# ---- credentials ------------------------------------------------------------


def test_read_credentials_returns_all_three(monkeypatch):
    _set_creds(monkeypatch)
    assert _read_credentials() == ("tenant-1", "client-1", "app-secret")


@pytest.mark.parametrize("missing", ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"])
def test_missing_each_credential_raises_naming_the_var(monkeypatch, missing):
    _set_creds(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(SharePointError) as exc:
        _read_credentials()
    assert missing in str(exc.value)


# ---- token acquisition ------------------------------------------------------


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_get_access_token_posts_client_credentials_and_returns_token(monkeypatch):
    captured: dict = {}

    def _fake_urlopen(request):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["data"] = request.data
        return _Resp(json.dumps({"access_token": "the-token", "expires_in": 3600}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    token = _get_access_token("tenant-1", "client-1", "app-secret")
    assert token == "the-token"
    # the token endpoint is scoped to the tenant
    assert "login.microsoftonline.com/tenant-1/oauth2/v2.0/token" in captured["url"]
    assert captured["method"] == "POST"
    # form-urlencoded client-credentials body with the .default scope
    form = parse_qs(captured["data"].decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_id"] == ["client-1"]
    assert form["client_secret"] == ["app-secret"]
    assert form["scope"] == ["https://graph.microsoft.com/.default"]


def _make_http_error(code: int, msg: str = "Error", body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://login.microsoftonline.com/t/oauth2/v2.0/token",
        code=code,
        msg=msg,
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )


def test_token_request_failure_is_clean_error(monkeypatch):
    def _boom(request):
        raise _make_http_error(401, "Unauthorized")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(SharePointError) as exc:
        _get_access_token("t", "c", "s")
    msg = str(exc.value)
    assert "401" in msg
    # points at the app-registration env vars
    assert "AZURE_CLIENT_SECRET" in msg or "AZURE_CLIENT_ID" in msg or "credentials" in msg.lower()


def test_token_request_failure_does_not_leak_secret(monkeypatch):
    # Even if the endpoint echoed the secret back in its error body, it must not
    # reach the raised message.
    def _boom(request):
        raise _make_http_error(400, "Bad Request", body=b'{"error":"invalid_client","secret":"super-secret"}')

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(SharePointError) as exc:
        _get_access_token("t", "c", "super-secret")
    assert "super-secret" not in str(exc.value)


def test_token_network_failure_is_clean_error(monkeypatch):
    def _boom(request):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(SharePointError) as exc:
        _get_access_token("t", "c", "s")
    assert "reach" in str(exc.value).lower() or "login" in str(exc.value).lower()


def test_token_response_without_access_token_is_clean_error(monkeypatch):
    def _fake_urlopen(request):
        return _Resp(json.dumps({"token_type": "Bearer"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    with pytest.raises(SharePointError) as exc:
        _get_access_token("t", "c", "s")
    assert "access_token" in str(exc.value)


# ---- graph GET (bearer) -----------------------------------------------------


def test_graph_get_sends_bearer_and_parses_json(monkeypatch):
    captured: dict = {}

    def _fake_urlopen(request):
        captured["headers"] = dict(request.headers)
        return _Resp(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    data = _graph_get_json("https://graph.microsoft.com/v1.0/sites/x", "the-token")
    assert data == {"ok": True}
    assert captured["headers"].get("Authorization") == "Bearer the-token"


def test_graph_get_bytes_returns_raw_body(monkeypatch):
    def _fake_urlopen(request):
        return _Resp(b"raw-file-bytes")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert _graph_get("https://graph.microsoft.com/v1.0/x/content", "tok") == b"raw-file-bytes"


def test_graph_get_401_names_permission(monkeypatch):
    def _boom(request):
        raise _make_http_error(403, "Forbidden")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(SharePointError) as exc:
        _graph_get_json("https://graph.microsoft.com/v1.0/sites/x", "tok")
    msg = str(exc.value)
    assert "403" in msg
    assert "Sites.Read.All" in msg or "permission" in msg.lower()


def test_graph_get_error_does_not_leak_token(monkeypatch):
    def _boom(request):
        raise _make_http_error(401)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(SharePointError) as exc:
        _graph_get_json("https://graph.microsoft.com/v1.0/sites/x", "super-secret-token")
    assert "super-secret-token" not in str(exc.value)


# ---- site resolution --------------------------------------------------------


def test_resolve_site_id_calls_graph_and_returns_id(monkeypatch):
    seen: dict = {}

    def fake_get_json(url, token):
        seen["url"] = url
        seen["token"] = token
        return {"id": "contoso.sharepoint.com,guid1,guid2", "name": "Engineering"}

    monkeypatch.setattr("corpuslint.sources.sharepoint._graph_get_json", fake_get_json)
    site_id = _resolve_site_id("contoso.sharepoint.com:/sites/Engineering", "tok")
    assert site_id == "contoso.sharepoint.com,guid1,guid2"
    # the site path is addressed via the hostname:/sites/path form
    assert seen["url"].endswith("/sites/contoso.sharepoint.com:/sites/Engineering")
    assert seen["token"] == "tok"


def test_resolve_site_id_missing_id_raises(monkeypatch):
    monkeypatch.setattr("corpuslint.sources.sharepoint._graph_get_json", lambda url, token: {})
    with pytest.raises(SharePointError):
        _resolve_site_id("contoso.sharepoint.com:/sites/X", "tok")


# ---- drive base + start url -------------------------------------------------


def test_drive_base_uses_site_default_drive_by_default():
    assert _drive_base("site-1", None) == "https://graph.microsoft.com/v1.0/sites/site-1/drive"


def test_drive_base_targets_explicit_drive_id():
    assert _drive_base("site-1", "drive-9") == "https://graph.microsoft.com/v1.0/drives/drive-9"


def test_start_url_is_root_children_without_folder():
    assert _start_url("BASE", None) == "BASE/root/children"


def test_start_url_scopes_to_folder_path():
    assert _start_url("BASE", "/Policies/HR/") == "BASE/root:/Policies/HR:/children"


# ---- drive traversal --------------------------------------------------------

_BASE = "https://graph.microsoft.com/v1.0/sites/site-1/drive"
_ROOT = f"{_BASE}/root/children"


def _children_url(item_id: str) -> str:
    return f"{_BASE}/items/{item_id}/children"


def test_traversal_recurses_folders_and_parses_supported_files():
    tree = {
        _ROOT: [
            _file_item("guide.md", "f1", web_url="https://contoso.sharepoint.com/guide.md"),
            _folder_item("sub", "d1"),
        ],
        _children_url("d1"): [
            _file_item("nested.txt", "f2", web_url="https://contoso.sharepoint.com/nested.txt"),
            _folder_item("deeper", "d2"),
        ],
        _children_url("d2"): [
            _file_item("page.html", "f3", web_url="https://contoso.sharepoint.com/page.html"),
        ],
    }
    bodies = {
        "f1": b"# Guide\n\nBody of the guide.",
        "f2": b"plain nested text",
        "f3": b"<h1>Title</h1><p>Html body</p>",
    }
    drive = _FakeDrive(tree, bodies)
    docs = _documents_from_drive(_BASE, _ROOT, drive.get_json, drive.download, _cfg())
    sources = {d.source for d in docs}
    assert sources == {
        "https://contoso.sharepoint.com/guide.md",
        "https://contoso.sharepoint.com/nested.txt",
        "https://contoso.sharepoint.com/page.html",
    }
    # every folder was recursed into
    assert _children_url("d1") in drive.listed_urls
    assert _children_url("d2") in drive.listed_urls


def test_traversal_filters_unsupported_extensions_without_downloading():
    tree = {
        _ROOT: [
            _file_item("keep.md", "f1"),
            _file_item("skip.png", "f2"),
            _file_item("skip.pdf", "f3"),  # loader has no PDF parser -> skipped
            _file_item("skip.zip", "f4"),
            _file_item("noext", "f5"),
        ]
    }
    bodies = {"f1": b"kept"}
    drive = _FakeDrive(tree, bodies)
    docs = _documents_from_drive(_BASE, _ROOT, drive.get_json, drive.download, _cfg())
    assert [d.text for d in docs] == ["kept"]
    # unsupported files were never downloaded (filtered before /content)
    assert drive.downloaded == ["f1"]


def test_traversal_paginates_listings_via_nextlink():
    files = [_file_item(f"doc{i}.md", f"f{i}") for i in range(250)]
    tree = {_ROOT: files}
    bodies = {f"f{i}": f"body {i}".encode() for i in range(250)}
    drive = _FakeDrive(tree, bodies, page_size=100)  # 250 -> 3 pages
    docs = _documents_from_drive(_BASE, _ROOT, drive.get_json, drive.download, _cfg())
    # nothing dropped across the paged listing (no silent cap)
    assert len(docs) == 250
    # the root listing was fetched three times (initial + two nextLink follows)
    assert drive.listed_urls.count(_ROOT) == 3


def test_html_file_is_run_through_the_html_extractor():
    tree = {_ROOT: [_file_item("policy.html", "f1", web_url="https://c.sharepoint.com/policy.html")]}
    bodies = {"f1": b"<h1>Refund policy</h1><p>Refunds take 5 days.</p>"}
    drive = _FakeDrive(tree, bodies)
    docs = _documents_from_drive(_BASE, _ROOT, drive.get_json, drive.download, _cfg())
    assert docs[0].text == "Refund policy Refunds take 5 days."
    assert "<h1>" not in docs[0].text


def test_file_without_weburl_falls_back_to_sharepoint_scheme():
    tree = {_ROOT: [_file_item("g.md", "item-42")]}  # no webUrl
    drive = _FakeDrive(tree, {"item-42": b"body"})
    docs = _documents_from_drive(_BASE, _ROOT, drive.get_json, drive.download, _cfg())
    assert docs[0].source.startswith("sharepoint://")
    assert "item-42" in docs[0].source


def test_recursion_depth_is_bounded_on_pathological_nesting():
    # A folder whose only child is a folder pointing back at the same listing URL
    # would recurse forever without a depth bound.
    loop_url = _children_url("loop")

    class _Loop:
        def __init__(self):
            self.calls = 0

        def get_json(self, url):
            self.calls += 1
            return {"value": [_folder_item("loop", "loop")]}

        def download(self, item_id):  # pragma: no cover - no files in the loop
            raise AssertionError("no files to download")

    loop = _Loop()
    # must return rather than hang / blow the stack
    docs = _documents_from_drive(_BASE, loop_url, loop.get_json, loop.download, _cfg())
    assert docs == []
    assert loop.calls < 1000  # bounded, not runaway


def test_per_file_download_error_skips_with_warning_others_kept():
    tree = {_ROOT: [_file_item("ok1.md", "f1"), _file_item("boom.md", "f2"), _file_item("ok2.md", "f3")]}
    bodies = {"f1": b"first", "f3": b"second"}

    class _Drive(_FakeDrive):
        def download(self, item_id):
            if item_id == "f2":
                raise OSError("connection reset")
            return super().download(item_id)

    drive = _Drive(tree, bodies)
    with pytest.warns(UserWarning, match="boom.md"):
        docs = _documents_from_drive(_BASE, _ROOT, drive.get_json, drive.download, _cfg())
    assert {d.text for d in docs} == {"first", "second"}


def test_per_file_warning_does_not_leak_secret_from_error_text():
    tree = {_ROOT: [_file_item("x.md", "f1")]}

    class _Drive(_FakeDrive):
        def download(self, item_id):
            raise OSError("token=SECRET-LEAK-123")

    drive = _Drive(tree, {})
    with pytest.warns(UserWarning) as record:
        _documents_from_drive(_BASE, _ROOT, drive.get_json, drive.download, _cfg())
    assert not any("SECRET-LEAK-123" in str(w.message) for w in record)


def test_temp_file_cleaned_up_even_on_parse_error(monkeypatch):
    """The finally block removes the temp file even when load_documents raises,
    and the file is then skipped-with-warning like any other per-file failure."""
    import corpuslint.sources.sharepoint as sp

    unlinked: list[str] = []
    real_unlink = sp.os.unlink

    def tracking_unlink(path):
        unlinked.append(path)
        real_unlink(path)

    monkeypatch.setattr(sp.os, "unlink", tracking_unlink)
    monkeypatch.setattr(
        sp, "load_documents", lambda paths, cfg: (_ for _ in ()).throw(RuntimeError("parser exploded"))
    )

    tree = {_ROOT: [_file_item("x.md", "f1")]}
    drive = _FakeDrive(tree, {"f1": b"data"})
    with pytest.warns(UserWarning, match="x.md"):
        docs = _documents_from_drive(_BASE, _ROOT, drive.get_json, drive.download, _cfg())

    assert docs == []
    assert len(unlinked) == 1
    import os as _os

    assert not _os.path.exists(unlinked[0])


# ---- load_sharepoint_documents: full wiring ---------------------------------


def test_load_resolves_site_walks_drive_and_reports_count(monkeypatch, capsys):
    _set_creds(monkeypatch)
    monkeypatch.setattr(
        "corpuslint.sources.sharepoint._get_access_token", lambda t, c, s: "tok"
    )
    monkeypatch.setattr(
        "corpuslint.sources.sharepoint._resolve_site_id", lambda site, token: "site-1"
    )

    root = "https://graph.microsoft.com/v1.0/sites/site-1/drive/root/children"

    def fake_get_json(url, token):
        assert token == "tok"
        if url == root:
            return {"value": [_file_item("a.md", "f1", web_url="https://c.sharepoint.com/a.md")]}
        return {"value": []}

    monkeypatch.setattr("corpuslint.sources.sharepoint._graph_get_json", fake_get_json)
    monkeypatch.setattr("corpuslint.sources.sharepoint._graph_get", lambda url, token: b"hello world")

    docs = load_sharepoint_documents(_cfg(source_options={"site": "contoso.sharepoint.com:/sites/Eng"}))
    assert docs == [Document(text="hello world", source="https://c.sharepoint.com/a.md")]
    captured = capsys.readouterr()
    assert "fetched 1" in captured.err
    assert captured.out == ""


def test_load_uses_site_id_without_resolving(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setattr("corpuslint.sources.sharepoint._get_access_token", lambda t, c, s: "tok")

    def _no_resolve(site, token):  # pragma: no cover - must not be called
        raise AssertionError("site resolution should be skipped when site_id is given")

    monkeypatch.setattr("corpuslint.sources.sharepoint._resolve_site_id", _no_resolve)
    root = "https://graph.microsoft.com/v1.0/sites/given-site/drive/root/children"
    monkeypatch.setattr(
        "corpuslint.sources.sharepoint._graph_get_json",
        lambda url, token: {"value": [_file_item("a.md", "f1")]} if url == root else {"value": []},
    )
    monkeypatch.setattr("corpuslint.sources.sharepoint._graph_get", lambda url, token: b"body")
    docs = load_sharepoint_documents(_cfg(source_options={"site_id": "given-site"}))
    assert len(docs) == 1


def test_load_missing_credential_raises_before_any_network(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)

    def _no_token(*a):  # pragma: no cover - must not be reached
        raise AssertionError("token request attempted with missing credentials")

    monkeypatch.setattr("corpuslint.sources.sharepoint._get_access_token", _no_token)
    with pytest.raises(SharePointError) as exc:
        load_sharepoint_documents(_cfg(source_options={"site_id": "s"}))
    assert "AZURE_CLIENT_SECRET" in str(exc.value)


# ---- SharePointSource: options resolution -----------------------------------


def test_source_requires_site_or_site_id():
    with pytest.raises(SourceError) as exc:
        SharePointSource().load(_cfg(source_options={}))
    msg = str(exc.value)
    assert "site" in msg


def test_source_reads_site_from_options(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setattr("corpuslint.sources.sharepoint._get_access_token", lambda t, c, s: "tok")
    monkeypatch.setattr("corpuslint.sources.sharepoint._resolve_site_id", lambda site, token: "site-1")
    root = "https://graph.microsoft.com/v1.0/sites/site-1/drive/root/children"
    monkeypatch.setattr(
        "corpuslint.sources.sharepoint._graph_get_json",
        lambda url, token: {"value": [_file_item("a.md", "f1", web_url="https://c/a.md")]}
        if url == root
        else {"value": []},
    )
    monkeypatch.setattr("corpuslint.sources.sharepoint._graph_get", lambda url, token: b"x")
    docs = SharePointSource().load(_cfg(source_options={"site": "contoso.sharepoint.com:/sites/Eng"}))
    assert docs[0].source == "https://c/a.md"


def test_source_scopes_to_drive_id_and_folder(monkeypatch):
    _set_creds(monkeypatch)
    monkeypatch.setattr("corpuslint.sources.sharepoint._get_access_token", lambda t, c, s: "tok")
    start = "https://graph.microsoft.com/v1.0/drives/drive-9/root:/Policies:/children"
    seen: dict = {}

    def fake_get_json(url, token):
        seen.setdefault("urls", []).append(url)
        return {"value": [_file_item("a.md", "f1")]} if url == start else {"value": []}

    monkeypatch.setattr("corpuslint.sources.sharepoint._graph_get_json", fake_get_json)
    monkeypatch.setattr("corpuslint.sources.sharepoint._graph_get", lambda url, token: b"x")
    docs = SharePointSource().load(
        _cfg(source_options={"site_id": "s", "drive_id": "drive-9", "folder": "Policies"})
    )
    assert len(docs) == 1
    assert start in seen["urls"]


# ---- registry ---------------------------------------------------------------


def test_get_source_returns_sharepoint_source():
    assert get_source("sharepoint").name == "sharepoint"
