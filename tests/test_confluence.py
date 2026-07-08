import io
import urllib.error
import urllib.request

import pytest

from corpuslint.config import Config
from corpuslint.models import Document
from corpuslint.sources.base import SourceError, get_source
from corpuslint.sources.confluence import (
    ConfluenceError,
    ConfluenceSource,
    _build_content_url,
    _documents_from_source,
    _http_get_json,
    load_confluence_documents,
)


def _cfg(**over) -> Config:
    cfg = Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def _page(page_id: str, title: str, body: str) -> dict:
    return {"id": page_id, "title": title, "body": {"storage": {"value": body}}}


class _FakeApi:
    """Serves paginated /content responses from a flat list of pages.

    Mimics Confluence's ``start``/``limit`` windowing so the pagination loop is
    exercised for real: each call returns the ``results`` slice plus the ``size``
    of that slice, and the loader must keep going until a short page arrives.
    """

    def __init__(self, pages: list[dict], limit: int = 50):
        self.pages = pages
        self.limit = limit
        self.calls: list[tuple[int, int]] = []

    def fetch(self, start: int, limit: int) -> dict:
        self.calls.append((start, limit))
        window = self.pages[start : start + limit]
        return {"results": window, "size": len(window), "limit": limit, "start": start}


# ---- pagination & mapping (inject fetch directly) ---------------------------


def test_paginates_over_all_pages_dropping_nothing():
    pages = [_page(str(i), f"T{i}", f"<p>body {i}</p>") for i in range(1, 121)]
    api = _FakeApi(pages, limit=50)
    docs = _documents_from_source("https://x.atlassian.net", "NCPCS", api.fetch, limit=50)
    # all 120 pages mapped, none dropped
    assert len(docs) == 120
    assert docs[0].text.startswith("T1 body 1")
    assert docs[-1].text.startswith("T120 body 120")
    # windowed correctly: 0, 50, 100 then a short final page stops the loop
    assert api.calls == [(0, 50), (50, 50), (100, 50)]


def test_stops_when_first_page_shorter_than_limit():
    api = _FakeApi([_page("1", "Only", "<p>hi</p>")], limit=50)
    docs = _documents_from_source("https://x.atlassian.net", "S", api.fetch, limit=50)
    assert len(docs) == 1
    assert api.calls == [(0, 50)]


def test_stops_cleanly_on_exact_multiple_of_limit():
    # 100 pages with limit 50: two full windows, then an empty window ends it.
    pages = [_page(str(i), f"T{i}", f"<p>{i}</p>") for i in range(100)]
    api = _FakeApi(pages, limit=50)
    docs = _documents_from_source("https://x.atlassian.net", "S", api.fetch, limit=50)
    assert len(docs) == 100
    assert api.calls == [(0, 50), (50, 50), (100, 50)]


def test_source_url_points_at_the_real_page():
    api = _FakeApi([_page("98765", "Runbook", "<p>x</p>")])
    docs = _documents_from_source("https://x.atlassian.net", "NCPCS", api.fetch)
    assert docs[0].source == "https://x.atlassian.net/wiki/spaces/NCPCS/pages/98765"


# ---- storage-format (XHTML + macros) -> clean text --------------------------


def test_storage_format_macros_and_tables_stripped_to_readable_text():
    body = (
        '<ac:structured-macro ac:name="info">'
        '<ac:rich-text-body><p>Heads up: rotate keys quarterly.</p></ac:rich-text-body>'
        "</ac:structured-macro>"
        "<table><tbody>"
        "<tr><th>Env</th><th>Owner</th></tr>"
        "<tr><td>prod</td><td>Alice</td></tr>"
        "</tbody></table>"
    )
    api = _FakeApi([_page("1", "Key Rotation", body)])
    docs = _documents_from_source("https://x.atlassian.net", "S", api.fetch)
    text = docs[0].text
    # macro/table tag names must be gone
    assert "ac:structured-macro" not in text
    assert "<table>" not in text
    assert "rich-text-body" not in text
    # the human-readable content survives, including the prepended title
    assert "Key Rotation" in text
    assert "rotate keys quarterly" in text
    assert "prod" in text
    assert "Alice" in text


# ---- empty-body handling ----------------------------------------------------


def test_empty_body_page_is_skipped_with_warning_others_kept():
    pages = [
        _page("1", "Good", "<p>real content here</p>"),
        _page("2", "Empty", "   "),
        {"id": "3", "title": "NoBody"},  # body key entirely absent
        _page("4", "AlsoGood", "<p>more content</p>"),
    ]
    api = _FakeApi(pages)
    with pytest.warns(UserWarning, match="empty body"):
        docs = _documents_from_source("https://x.atlassian.net", "S", api.fetch)
    assert [d.source.rsplit("/", 1)[-1] for d in docs] == ["1", "4"]


# ---- credentials / config wiring (load_confluence_documents) ----------------


def test_load_reads_env_creds_and_reports_count(monkeypatch, capsys):
    monkeypatch.setenv("CONFLUENCE_EMAIL", "me@corp.example")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok-123")
    seen: dict = {}

    def fake_http(url, email, api_token):
        seen["url"] = url
        seen["email"] = email
        seen["api_token"] = api_token
        return {"results": [_page("1", "Hi", "<p>yo</p>")], "size": 1, "limit": 50, "start": 0}

    monkeypatch.setattr("corpuslint.sources.confluence._http_get_json", fake_http)
    docs = load_confluence_documents("https://x.atlassian.net", "NCPCS", _cfg())
    assert [d.text for d in docs] == ["Hi yo"]
    # creds flowed from env into the HTTP layer
    assert seen["email"] == "me@corp.example"
    assert seen["api_token"] == "tok-123"
    # the request URL targets the content API for the requested space
    assert "spaceKey=NCPCS" in seen["url"]
    assert "expand=body.storage" in seen["url"]
    # count reported on stderr, stdout stays clean (for --json)
    captured = capsys.readouterr()
    assert "fetched 1" in captured.err
    assert captured.out == ""


def test_missing_email_env_raises_naming_the_var(monkeypatch):
    monkeypatch.delenv("CONFLUENCE_EMAIL", raising=False)
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")
    with pytest.raises(ConfluenceError) as exc:
        load_confluence_documents("https://x.atlassian.net", "S", _cfg())
    assert "CONFLUENCE_EMAIL" in str(exc.value)


def test_missing_token_env_raises_naming_the_var(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_EMAIL", "me@corp.example")
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)
    with pytest.raises(ConfluenceError) as exc:
        load_confluence_documents("https://x.atlassian.net", "S", _cfg())
    assert "CONFLUENCE_API_TOKEN" in str(exc.value)


def test_error_does_not_leak_token_value(monkeypatch):
    monkeypatch.delenv("CONFLUENCE_EMAIL", raising=False)
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "super-secret-token")
    with pytest.raises(ConfluenceError) as exc:
        load_confluence_documents("https://x.atlassian.net", "S", _cfg())
    assert "super-secret-token" not in str(exc.value)


# ---- ConfluenceSource: options resolution -----------------------------------


def test_source_requires_space():
    with pytest.raises(SourceError, match="space"):
        ConfluenceSource().load(_cfg(source_options={"base_url": "https://x.atlassian.net"}))


def test_source_requires_base_url_from_opts_or_env(monkeypatch):
    monkeypatch.delenv("CONFLUENCE_BASE_URL", raising=False)
    with pytest.raises(SourceError) as exc:
        ConfluenceSource().load(_cfg(source_options={"space": "NCPCS"}))
    msg = str(exc.value)
    assert "base_url" in msg or "CONFLUENCE_BASE_URL" in msg


def test_source_reads_base_url_from_env_when_not_in_opts(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://env.atlassian.net")
    monkeypatch.setenv("CONFLUENCE_EMAIL", "me@corp.example")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")
    monkeypatch.setattr(
        "corpuslint.sources.confluence._http_get_json",
        lambda url, email, api_token: {
            "results": [_page("7", "Env", "<p>from env</p>")],
            "size": 1,
            "limit": 50,
            "start": 0,
        },
    )
    docs = ConfluenceSource().load(_cfg(source_options={"space": "NCPCS"}))
    assert docs[0].source == "https://env.atlassian.net/wiki/spaces/NCPCS/pages/7"


def test_trailing_slash_in_base_url_is_normalised(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_EMAIL", "me@corp.example")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")
    monkeypatch.setattr(
        "corpuslint.sources.confluence._http_get_json",
        lambda url, email, api_token: {
            "results": [_page("1", "T", "<p>b</p>")],
            "size": 1,
            "limit": 50,
            "start": 0,
        },
    )
    docs = ConfluenceSource().load(
        _cfg(source_options={"space": "S", "base_url": "https://x.atlassian.net/"})
    )
    # no doubled slash in the traceable source URL
    assert docs[0].source == "https://x.atlassian.net/wiki/spaces/S/pages/1"


# ---- URL builder ------------------------------------------------------------


def test_build_content_url_encodes_expected_query():
    url = _build_content_url("https://x.atlassian.net", "NCPCS", start=100, limit=50)
    assert url.startswith("https://x.atlassian.net/wiki/rest/api/content?")
    assert "spaceKey=NCPCS" in url
    assert "type=page" in url
    assert "status=current" in url
    assert "expand=body.storage" in url
    assert "limit=50" in url
    assert "start=100" in url


# ---- registry ---------------------------------------------------------------


def test_get_source_returns_confluence_source():
    src = get_source("confluence")
    assert src.name == "confluence"


def test_confluence_document_type():
    api = _FakeApi([_page("1", "T", "<p>b</p>")])
    docs = _documents_from_source("https://x.atlassian.net", "S", api.fetch)
    assert isinstance(docs[0], Document)


# ---- HTTP error paths (_http_get_json) --------------------------------------


def _make_http_error(code: int, msg: str = "Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://x.atlassian.net/wiki/rest/api/content",
        code=code,
        msg=msg,
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )


def test_http_401_raises_confluence_error_naming_creds(monkeypatch):
    def _boom(req):
        raise _make_http_error(401)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(ConfluenceError) as exc:
        _http_get_json("https://x.atlassian.net/wiki/rest/api/content", "me@corp.example", "tok")
    msg = str(exc.value)
    assert "401" in msg
    assert "CONFLUENCE_EMAIL" in msg or "CONFLUENCE_API_TOKEN" in msg or "credentials" in msg.lower()


def test_http_403_raises_confluence_error_naming_creds(monkeypatch):
    def _boom(req):
        raise _make_http_error(403)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(ConfluenceError) as exc:
        _http_get_json("https://x.atlassian.net/wiki/rest/api/content", "me@corp.example", "tok")
    msg = str(exc.value)
    assert "403" in msg
    assert "CONFLUENCE_EMAIL" in msg or "CONFLUENCE_API_TOKEN" in msg or "credentials" in msg.lower()


def test_http_500_raises_confluence_error_with_code(monkeypatch):
    def _boom(req):
        raise _make_http_error(500, "Internal Server Error")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(ConfluenceError) as exc:
        _http_get_json("https://x.atlassian.net/wiki/rest/api/content", "me@corp.example", "tok")
    assert "500" in str(exc.value)


def test_http_404_raises_confluence_error_with_code(monkeypatch):
    def _boom(req):
        raise _make_http_error(404, "Not Found")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(ConfluenceError) as exc:
        _http_get_json("https://x.atlassian.net/wiki/rest/api/content", "me@corp.example", "tok")
    assert "404" in str(exc.value)


def test_url_error_raises_confluence_error_without_traceback(monkeypatch):
    def _boom(req):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(ConfluenceError) as exc:
        _http_get_json("https://x.atlassian.net/wiki/rest/api/content", "me@corp.example", "tok")
    assert "reach" in str(exc.value).lower() or "confluence" in str(exc.value).lower()


def test_http_error_does_not_leak_api_token(monkeypatch):
    def _boom(req):
        raise _make_http_error(401)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(ConfluenceError) as exc:
        _http_get_json("https://x.atlassian.net/wiki/rest/api/content", "me@corp.example", "super-secret-token")
    assert "super-secret-token" not in str(exc.value)
