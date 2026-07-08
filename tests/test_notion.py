import io
import json
import urllib.error
import urllib.request

import pytest

from corpuslint.config import Config
from corpuslint.models import Document
from corpuslint.sources.base import SourceError, get_source
from corpuslint.sources.notion import (
    NotionError,
    NotionSource,
    _blocks_to_text,
    _documents_from_database,
    _http_request_json,
    _page_source,
    _page_title,
    _query_database,
    load_notion_documents,
)


def _cfg(**over) -> Config:
    cfg = Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def _rt(text: str) -> list[dict]:
    """A minimal Notion rich_text array carrying one plain_text run."""
    return [{"plain_text": text}] if text else []


def _block(btype: str, text: str, block_id: str = "b", has_children: bool = False) -> dict:
    return {
        "id": block_id,
        "type": btype,
        btype: {"rich_text": _rt(text)},
        "has_children": has_children,
    }


def _page(page_id: str, title: str, url: str | None = None) -> dict:
    page = {
        "id": page_id,
        "properties": {
            "Name": {"type": "title", "title": _rt(title)},
        },
    }
    if url is not None:
        page["url"] = url
    return page


class _FakeQuery:
    """Serves paginated /databases/{id}/query responses from a flat page list.

    Mimics Notion's opaque ``start_cursor``/``has_more``/``next_cursor`` paging so
    the loop is exercised for real: it must follow the cursor until ``has_more`` is
    false. Cursors here are stringified offsets so the fake stays simple.
    """

    def __init__(self, pages: list[dict], page_size: int = 100):
        self.pages = pages
        self.page_size = page_size
        self.cursors: list[str | None] = []

    def fetch(self, cursor: str | None) -> dict:
        self.cursors.append(cursor)
        start = int(cursor) if cursor else 0
        window = self.pages[start : start + self.page_size]
        nxt = start + self.page_size
        has_more = nxt < len(self.pages)
        return {
            "results": window,
            "has_more": has_more,
            "next_cursor": str(nxt) if has_more else None,
        }


class _FakeBlocks:
    """Serves paginated /blocks/{id}/children from a tree of block_id -> children."""

    def __init__(self, tree: dict[str, list[dict]], page_size: int = 100):
        self.tree = tree
        self.page_size = page_size
        self.calls: list[tuple[str, str | None]] = []

    def fetch(self, block_id: str, cursor: str | None) -> dict:
        self.calls.append((block_id, cursor))
        children = self.tree.get(block_id, [])
        start = int(cursor) if cursor else 0
        window = children[start : start + self.page_size]
        nxt = start + self.page_size
        has_more = nxt < len(children)
        return {
            "results": window,
            "has_more": has_more,
            "next_cursor": str(nxt) if has_more else None,
        }


# ---- database query pagination ----------------------------------------------


def test_query_database_paginates_over_all_pages_dropping_nothing():
    pages = [_page(str(i), f"T{i}") for i in range(250)]
    q = _FakeQuery(pages, page_size=100)
    fetched = _query_database("db-1", q.fetch)
    assert len(fetched) == 250
    # followed the cursor: first call None, then the next_cursor from each page
    assert q.cursors == [None, "100", "200"]


def test_query_database_single_short_page_stops_immediately():
    q = _FakeQuery([_page("1", "Only")], page_size=100)
    fetched = _query_database("db", q.fetch)
    assert len(fetched) == 1
    assert q.cursors == [None]


# ---- block -> text ----------------------------------------------------------


def test_mixed_block_types_concatenate_to_text():
    tree = {
        "pg": [
            _block("heading_1", "Deploy Guide", "h"),
            _block("paragraph", "Roll keys quarterly.", "p"),
            _block("bulleted_list_item", "first bullet", "b1"),
            _block("numbered_list_item", "step one", "n1"),
            _block("to_do", "ship it", "t1"),
            _block("quote", "wise words", "q1"),
            _block("callout", "heads up", "c1"),
            _block("code", "print('hi')", "code1"),
        ]
    }
    blocks = _FakeBlocks(tree)
    text = _blocks_to_text("pg", blocks.fetch)
    for expected in [
        "Deploy Guide",
        "Roll keys quarterly.",
        "first bullet",
        "step one",
        "ship it",
        "wise words",
        "heads up",
        "print('hi')",
    ]:
        assert expected in text


def test_unsupported_block_types_are_ignored():
    tree = {"pg": [_block("paragraph", "kept", "p"), {"id": "d", "type": "divider", "divider": {}}]}
    blocks = _FakeBlocks(tree)
    text = _blocks_to_text("pg", blocks.fetch)
    assert "kept" in text
    assert "divider" not in text


def test_block_children_paginate_fully():
    # 250 sibling paragraphs under one page, page_size 100 -> 3 fetches, none lost
    children = [_block("paragraph", f"line {i}", f"b{i}") for i in range(250)]
    blocks = _FakeBlocks({"pg": children}, page_size=100)
    text = _blocks_to_text("pg", blocks.fetch)
    assert "line 0" in text
    assert "line 249" in text
    assert text.count("line ") == 250
    # cursor followed across all three windows
    assert blocks.calls == [("pg", None), ("pg", "100"), ("pg", "200")]


def test_nested_children_are_recursed():
    tree = {
        "pg": [_block("paragraph", "parent", "parent", has_children=True)],
        "parent": [_block("paragraph", "nested child", "child")],
    }
    blocks = _FakeBlocks(tree)
    text = _blocks_to_text("pg", blocks.fetch)
    assert "parent" in text
    assert "nested child" in text


def test_recursion_depth_is_bounded_on_pathological_nesting():
    # Every block claims to have a child that is itself -> infinite without a bound.
    class _Infinite:
        def __init__(self):
            self.calls = 0

        def fetch(self, block_id, cursor):
            self.calls += 1
            return {
                "results": [_block("paragraph", "deep", "loop", has_children=True)],
                "has_more": False,
                "next_cursor": None,
            }

    inf = _Infinite()
    text = _blocks_to_text("start", inf.fetch)  # must return, not hang/recurse forever
    assert "deep" in text
    # bounded: far fewer calls than the (self-referential) tree would allow
    assert inf.calls < 100


# ---- page title -------------------------------------------------------------


def test_page_title_pulled_from_the_title_property():
    page = _page("1", "Onboarding Runbook")
    assert _page_title(page) == "Onboarding Runbook"


def test_page_title_empty_when_no_title_property():
    assert _page_title({"id": "1", "properties": {"Tags": {"type": "multi_select"}}}) == ""


# ---- source url -------------------------------------------------------------


def test_source_url_prefers_page_url():
    page = _page("abc", "T", url="https://www.notion.so/My-Page-abc")
    assert _page_source(page) == "https://www.notion.so/My-Page-abc"


def test_source_url_falls_back_to_notion_scheme():
    page = _page("abc123", "T")
    assert _page_source(page) == "notion://abc123"


# ---- document assembly ------------------------------------------------------


def test_documents_combine_title_and_body():
    pages = [_page("pg", "The Title", url="https://notion.so/pg")]
    q = _FakeQuery(pages)
    tree = {"pg": [_block("paragraph", "the body text", "p")]}
    blocks = _FakeBlocks(tree)
    docs = _documents_from_database("db", q.fetch, blocks.fetch)
    assert len(docs) == 1
    assert "The Title" in docs[0].text
    assert "the body text" in docs[0].text
    assert docs[0].source == "https://notion.so/pg"
    assert isinstance(docs[0], Document)


def test_empty_page_is_skipped_with_warning_others_kept():
    pages = [
        _page("good", "Has Content", url="https://notion.so/good"),
        _page("", "", url=None),  # no title, no id, no blocks
    ]
    q = _FakeQuery(pages)
    tree = {"good": [_block("paragraph", "real content", "p")]}
    blocks = _FakeBlocks(tree)
    with pytest.warns(UserWarning, match="empty"):
        docs = _documents_from_database("db", q.fetch, blocks.fetch)
    assert [d.text for d in docs] == ["Has Content\nreal content"] or "real content" in docs[0].text
    assert len(docs) == 1


def test_page_with_title_but_no_blocks_is_kept():
    pages = [_page("pg", "Title Only", url="https://notion.so/pg")]
    q = _FakeQuery(pages)
    blocks = _FakeBlocks({})  # no children for anything
    docs = _documents_from_database("db", q.fetch, blocks.fetch)
    assert len(docs) == 1
    assert "Title Only" in docs[0].text


# ---- credentials / config wiring --------------------------------------------


def test_load_reads_token_from_env_and_reports_count(monkeypatch, capsys):
    monkeypatch.setenv("NOTION_TOKEN", "secret_tok")
    seen: dict = {}

    def fake_http(url, token, method="GET", payload=None):
        seen.setdefault("tokens", []).append(token)
        seen.setdefault("urls", []).append(url)
        if "/query" in url:
            return {
                "results": [_page("pg", "Hi", url="https://notion.so/pg")],
                "has_more": False,
                "next_cursor": None,
            }
        return {"results": [_block("paragraph", "yo", "p")], "has_more": False, "next_cursor": None}

    monkeypatch.setattr("corpuslint.sources.notion._http_request_json", fake_http)
    docs = load_notion_documents("db-42", _cfg())
    assert "Hi" in docs[0].text
    assert "yo" in docs[0].text
    # token flowed from env into the HTTP layer, and never appears in output
    assert seen["tokens"][0] == "secret_tok"
    assert any("databases/db-42/query" in u for u in seen["urls"])
    captured = capsys.readouterr()
    assert "fetched 1" in captured.err
    assert captured.out == ""


def test_missing_token_env_raises_naming_the_var(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(NotionError) as exc:
        load_notion_documents("db", _cfg())
    assert "NOTION_TOKEN" in str(exc.value)


# ---- NotionSource: options resolution ---------------------------------------


def test_source_requires_database_id():
    with pytest.raises(SourceError, match="database"):
        NotionSource().load(_cfg(source_options={}))


def test_source_reads_database_id_from_opts(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setattr(
        "corpuslint.sources.notion._http_request_json",
        lambda url, token, method="GET", payload=None: (
            {"results": [_page("pg", "T", url="https://notion.so/pg")], "has_more": False, "next_cursor": None}
            if "/query" in url
            else {"results": [_block("paragraph", "body", "p")], "has_more": False, "next_cursor": None}
        ),
    )
    docs = NotionSource().load(_cfg(source_options={"database_id": "db-9"}))
    assert docs[0].source == "https://notion.so/pg"


# ---- registry ---------------------------------------------------------------


def test_get_source_returns_notion_source():
    src = get_source("notion")
    assert src.name == "notion"


# ---- HTTP layer -------------------------------------------------------------


def _make_http_error(code: int, msg: str = "Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.notion.com/v1/databases/db/query",
        code=code,
        msg=msg,
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )


def test_http_sends_bearer_auth_and_version_header(monkeypatch):
    captured: dict = {}

    class _Resp:
        def read(self):
            return json.dumps({"results": [], "has_more": False}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(request):
        captured["headers"] = dict(request.headers)
        captured["method"] = request.get_method()
        captured["data"] = request.data
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    _http_request_json(
        "https://api.notion.com/v1/databases/db/query",
        "secret_tok",
        method="POST",
        payload={"page_size": 100},
    )
    # header keys are title-cased by urllib
    assert captured["headers"].get("Authorization") == "Bearer secret_tok"
    assert captured["headers"].get("Notion-version") == "2022-06-28"
    assert captured["method"] == "POST"
    assert json.loads(captured["data"]) == {"page_size": 100}


def test_http_401_raises_notion_error_naming_token(monkeypatch):
    def _boom(req):
        raise _make_http_error(401)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(NotionError) as exc:
        _http_request_json("https://api.notion.com/v1/databases/db/query", "tok", method="POST", payload={})
    msg = str(exc.value)
    assert "401" in msg
    assert "NOTION_TOKEN" in msg or "credentials" in msg.lower()


def test_http_404_raises_notion_error_with_code(monkeypatch):
    def _boom(req):
        raise _make_http_error(404, "Not Found")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(NotionError) as exc:
        _http_request_json("https://api.notion.com/v1/databases/db/query", "tok", method="POST", payload={})
    assert "404" in str(exc.value)


def test_url_error_raises_notion_error_without_traceback(monkeypatch):
    def _boom(req):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(NotionError) as exc:
        _http_request_json("https://api.notion.com/v1/databases/db/query", "tok")
    assert "reach" in str(exc.value).lower() or "notion" in str(exc.value).lower()


def test_http_error_does_not_leak_token(monkeypatch):
    def _boom(req):
        raise _make_http_error(401)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(NotionError) as exc:
        _http_request_json(
            "https://api.notion.com/v1/databases/db/query", "super-secret-token", method="POST", payload={}
        )
    assert "super-secret-token" not in str(exc.value)
