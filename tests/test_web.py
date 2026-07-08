import io
import urllib.error
import urllib.request

import pytest

from corpuslint.config import Config
from corpuslint.models import Document
from corpuslint.sources.base import SourceError, get_source
from corpuslint.sources.web import (
    WebError,
    WebSource,
    _collect_sitemap_urls,
    _extract_links,
    _http_fetch,
    _parse_sitemap,
    _strip_fragment,
    load_web_crawl,
    load_web_sitemap,
)


def _cfg(**over) -> Config:
    cfg = Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


class _FakeWeb:
    """Serves canned (content_type, body) per URL; the single mocked network seam.

    - ``pages``: url -> body(str, served as text/html) or (content_type, body).
    - ``robots``: base ("https://host") -> robots.txt body. Absent = allow all.
    - Unknown URLs raise WebError (mimics a 404 / unreachable host).
    Records every fetched URL so tests can assert dedup / same-domain / caps.
    """

    def __init__(self, pages=None, robots=None):
        self.pages = {}
        for url, val in (pages or {}).items():
            self.pages[url] = val if isinstance(val, tuple) else ("text/html", val)
        self.robots = robots or {}
        self.fetched: list[str] = []

    def fetch(self, url: str):
        self.fetched.append(url)
        if url.endswith("/robots.txt"):
            base = url[: -len("/robots.txt")]
            return ("text/plain", self.robots.get(base, ""))
        if url in self.pages:
            return self.pages[url]
        raise WebError(f"could not fetch {url}")


# ---- sitemap parsing --------------------------------------------------------


_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.x.com/a</loc></url>
  <url><loc>https://docs.x.com/b</loc></url>
</urlset>"""

_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://docs.x.com/sitemap-1.xml</loc></sitemap>
  <sitemap><loc>https://docs.x.com/sitemap-2.xml</loc></sitemap>
</sitemapindex>"""


def test_parse_sitemap_urlset_returns_page_locs():
    locs, is_index = _parse_sitemap(_URLSET)
    assert is_index is False
    assert locs == ["https://docs.x.com/a", "https://docs.x.com/b"]


def test_parse_sitemap_index_returns_nested_sitemaps():
    locs, is_index = _parse_sitemap(_INDEX)
    assert is_index is True
    assert locs == ["https://docs.x.com/sitemap-1.xml", "https://docs.x.com/sitemap-2.xml"]


def test_parse_sitemap_invalid_xml_raises_web_error():
    with pytest.raises(WebError):
        _parse_sitemap("<not xml <<<")


def test_parse_sitemap_rejects_doctype_to_block_entity_expansion():
    # Billion-laughs / XXE vectors ride in via a DOCTYPE + entity decls. Real
    # sitemaps never carry a DOCTYPE, so we refuse them before parsing.
    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;">]>'
        "<urlset><url><loc>&lol2;</loc></url></urlset>"
    )
    with pytest.raises(WebError):
        _parse_sitemap(bomb)


# ---- sitemap URL collection (nested index, bounded) -------------------------


def test_collect_sitemap_urls_follows_nested_index():
    web = _FakeWeb(
        pages={
            "https://docs.x.com/sitemap.xml": ("application/xml", _INDEX),
            "https://docs.x.com/sitemap-1.xml": ("application/xml", _URLSET),
            "https://docs.x.com/sitemap-2.xml": (
                "application/xml",
                _URLSET.replace("/a", "/c").replace("/b", "/d"),
            ),
        }
    )
    urls, truncated = _collect_sitemap_urls("https://docs.x.com/sitemap.xml", web.fetch, 200)
    assert truncated is False
    assert set(urls) == {
        "https://docs.x.com/a",
        "https://docs.x.com/b",
        "https://docs.x.com/c",
        "https://docs.x.com/d",
    }


def test_collect_sitemap_urls_dedupes_repeated_locs():
    dup = _URLSET.replace("/b", "/a")  # both entries now point at /a
    web = _FakeWeb(pages={"https://docs.x.com/sitemap.xml": ("application/xml", dup)})
    urls, _ = _collect_sitemap_urls("https://docs.x.com/sitemap.xml", web.fetch, 200)
    assert urls == ["https://docs.x.com/a"]


def test_collect_sitemap_urls_bounded_by_max_pages():
    big = "".join(f"<url><loc>https://docs.x.com/p{i}</loc></url>" for i in range(50))
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + big + "</urlset>"
    )
    web = _FakeWeb(pages={"https://docs.x.com/sitemap.xml": ("application/xml", xml)})
    urls, truncated = _collect_sitemap_urls("https://docs.x.com/sitemap.xml", web.fetch, 10)
    assert len(urls) == 10
    assert truncated is True


# ---- sitemap mode: load_web_sitemap -----------------------------------------


def _sitemap_cfg(**opts):
    base = {"delay": "0"}
    base.update(opts)
    return _cfg(source_options=base)


def test_sitemap_mode_fetches_each_page_to_documents():
    web = _FakeWeb(
        pages={
            "https://docs.x.com/sitemap.xml": ("application/xml", _URLSET),
            "https://docs.x.com/a": "<h1>Alpha</h1><p>first page</p>",
            "https://docs.x.com/b": "<p>second page</p>",
        }
    )
    docs = load_web_sitemap("https://docs.x.com/sitemap.xml", _sitemap_cfg(), fetch=web.fetch)
    assert [d.source for d in docs] == ["https://docs.x.com/a", "https://docs.x.com/b"]
    assert "Alpha" in docs[0].text and "first page" in docs[0].text
    assert "second page" in docs[1].text
    assert all(isinstance(d, Document) for d in docs)


def test_sitemap_mode_skips_non_html_content_type_with_warning():
    web = _FakeWeb(
        pages={
            "https://docs.x.com/sitemap.xml": ("application/xml", _URLSET),
            "https://docs.x.com/a": ("application/pdf", "%PDF-1.7 binary"),
            "https://docs.x.com/b": "<p>kept</p>",
        }
    )
    with pytest.warns(UserWarning, match="non-HTML"):
        docs = load_web_sitemap("https://docs.x.com/sitemap.xml", _sitemap_cfg(), fetch=web.fetch)
    assert [d.source for d in docs] == ["https://docs.x.com/b"]


def test_sitemap_mode_per_page_error_skips_and_continues():
    web = _FakeWeb(
        pages={
            "https://docs.x.com/sitemap.xml": ("application/xml", _URLSET),
            # /a is intentionally absent -> fetch raises WebError
            "https://docs.x.com/b": "<p>still here</p>",
        }
    )
    with pytest.warns(UserWarning):
        docs = load_web_sitemap("https://docs.x.com/sitemap.xml", _sitemap_cfg(), fetch=web.fetch)
    assert [d.source for d in docs] == ["https://docs.x.com/b"]


def test_sitemap_mode_respects_robots_disallow():
    web = _FakeWeb(
        pages={
            "https://docs.x.com/sitemap.xml": ("application/xml", _URLSET),
            "https://docs.x.com/a": "<p>private</p>",
            "https://docs.x.com/b": "<p>public</p>",
        },
        robots={"https://docs.x.com": "User-agent: *\nDisallow: /a"},
    )
    with pytest.warns(UserWarning, match="robots"):
        docs = load_web_sitemap("https://docs.x.com/sitemap.xml", _sitemap_cfg(), fetch=web.fetch)
    assert [d.source for d in docs] == ["https://docs.x.com/b"]


def test_sitemap_mode_reports_count_on_stderr(capsys):
    web = _FakeWeb(
        pages={
            "https://docs.x.com/sitemap.xml": ("application/xml", _URLSET),
            "https://docs.x.com/a": "<p>one</p>",
            "https://docs.x.com/b": "<p>two</p>",
        }
    )
    load_web_sitemap("https://docs.x.com/sitemap.xml", _sitemap_cfg(), fetch=web.fetch)
    captured = capsys.readouterr()
    assert "fetched 2" in captured.err
    assert captured.out == ""


# ---- link extraction --------------------------------------------------------


def test_extract_links_resolves_relative_and_strips_fragment():
    html = (
        '<a href="/docs/a">A</a>'
        '<a href="b.html#section">B</a>'
        '<a href="https://docs.x.com/c">C</a>'
        '<a href="mailto:x@y.com">mail</a>'  # non-http -> dropped
    )
    links = _extract_links(html, "https://docs.x.com/docs/")
    assert "https://docs.x.com/docs/a" in links
    assert "https://docs.x.com/docs/b.html" in links  # fragment stripped
    assert "https://docs.x.com/c" in links
    assert not any(link.startswith("mailto:") for link in links)


# ---- crawl mode: BFS + guardrails -------------------------------------------


def _crawl_cfg(**opts):
    base = {"delay": "0"}
    base.update(opts)
    return _cfg(source_options=base)


def test_crawl_bfs_respects_depth():
    web = _FakeWeb(
        pages={
            "https://x.com/": '<a href="/a">a</a>',
            "https://x.com/a": '<a href="/b">b</a>',
            "https://x.com/b": "<p>deep</p>",
        }
    )
    docs = load_web_crawl("https://x.com/", _crawl_cfg(depth="1"), fetch=web.fetch)
    sources = {d.source for d in docs}
    assert sources == {"https://x.com/", "https://x.com/a"}  # /b is at depth 2, excluded


def test_crawl_bfs_reaches_deeper_with_higher_depth():
    web = _FakeWeb(
        pages={
            "https://x.com/": '<a href="/a">a</a>',
            "https://x.com/a": '<a href="/b">b</a>',
            "https://x.com/b": "<p>deep</p>",
        }
    )
    docs = load_web_crawl("https://x.com/", _crawl_cfg(depth="2"), fetch=web.fetch)
    assert {d.source for d in docs} == {"https://x.com/", "https://x.com/a", "https://x.com/b"}


def test_crawl_respects_max_pages_and_warns_on_truncation():
    links = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(20))
    pages = {"https://x.com/": links}
    for i in range(20):
        pages[f"https://x.com/p{i}"] = f"<p>page {i}</p>"
    web = _FakeWeb(pages=pages)
    with pytest.warns(UserWarning, match="max_pages"):
        docs = load_web_crawl("https://x.com/", _crawl_cfg(depth="3", max_pages="5"), fetch=web.fetch)
    assert len(docs) == 5


def test_crawl_stays_on_same_domain():
    web = _FakeWeb(
        pages={
            "https://x.com/": '<a href="https://evil.com/x">ext</a><a href="/inside">in</a>',
            "https://x.com/inside": "<p>internal</p>",
            "https://evil.com/x": "<p>should NOT be fetched</p>",
        }
    )
    docs = load_web_crawl("https://x.com/", _crawl_cfg(depth="2"), fetch=web.fetch)
    assert {d.source for d in docs} == {"https://x.com/", "https://x.com/inside"}
    assert "https://evil.com/x" not in web.fetched


def test_crawl_dedupes_visited_urls():
    web = _FakeWeb(
        pages={
            "https://x.com/": '<a href="/a">a</a><a href="/a">a-again</a>',
            "https://x.com/a": '<a href="/">home</a>',  # links back to start
        }
    )
    load_web_crawl("https://x.com/", _crawl_cfg(depth="3"), fetch=web.fetch)
    page_fetches = [u for u in web.fetched if not u.endswith("/robots.txt")]
    assert page_fetches.count("https://x.com/") == 1
    assert page_fetches.count("https://x.com/a") == 1


def test_crawl_respects_robots_disallow():
    web = _FakeWeb(
        pages={
            "https://x.com/": '<a href="/private">p</a><a href="/public">pub</a>',
            "https://x.com/private": "<p>secret</p>",
            "https://x.com/public": "<p>open</p>",
        },
        robots={"https://x.com": "User-agent: *\nDisallow: /private"},
    )
    with pytest.warns(UserWarning, match="robots"):
        docs = load_web_crawl("https://x.com/", _crawl_cfg(depth="2"), fetch=web.fetch)
    assert {d.source for d in docs} == {"https://x.com/", "https://x.com/public"}
    assert "https://x.com/private" not in web.fetched


def test_crawl_per_page_error_skips_and_continues():
    web = _FakeWeb(
        pages={
            "https://x.com/": '<a href="/broken">x</a><a href="/ok">y</a>',
            # /broken absent -> WebError
            "https://x.com/ok": "<p>fine</p>",
        }
    )
    with pytest.warns(UserWarning):
        docs = load_web_crawl("https://x.com/", _crawl_cfg(depth="2"), fetch=web.fetch)
    assert "https://x.com/ok" in {d.source for d in docs}


def test_crawl_skips_non_html_content_type():
    web = _FakeWeb(
        pages={
            "https://x.com/": '<a href="/doc.pdf">pdf</a><a href="/page">p</a>',
            "https://x.com/doc.pdf": ("application/pdf", "%PDF binary"),
            "https://x.com/page": "<p>html page</p>",
        }
    )
    with pytest.warns(UserWarning, match="non-HTML"):
        docs = load_web_crawl("https://x.com/", _crawl_cfg(depth="2"), fetch=web.fetch)
    assert {d.source for d in docs} == {"https://x.com/", "https://x.com/page"}


# ---- WebSource: options resolution ------------------------------------------


def test_source_requires_sitemap_or_url():
    with pytest.raises(SourceError, match="sitemap"):
        WebSource().load(_cfg(source_options={}))


def test_source_routes_to_sitemap_mode(monkeypatch):
    called = {}
    monkeypatch.setattr(
        "corpuslint.sources.web.load_web_sitemap",
        lambda url, cfg, fetch=None: called.setdefault("sitemap", url) or [],
    )
    WebSource().load(_cfg(source_options={"sitemap": "https://docs.x.com/sitemap.xml"}))
    assert called["sitemap"] == "https://docs.x.com/sitemap.xml"


def test_source_routes_to_crawl_mode(monkeypatch):
    called = {}
    monkeypatch.setattr(
        "corpuslint.sources.web.load_web_crawl",
        lambda url, cfg, fetch=None: called.setdefault("crawl", url) or [],
    )
    WebSource().load(_cfg(source_options={"url": "https://docs.x.com/"}))
    assert called["crawl"] == "https://docs.x.com/"


# ---- registry ---------------------------------------------------------------


def test_get_source_returns_web_source():
    src = get_source("web")
    assert src.name == "web"


# ---- helpers ----------------------------------------------------------------


def test_strip_fragment_removes_hash():
    assert _strip_fragment("https://x.com/a#top") == "https://x.com/a"
    assert _strip_fragment("https://x.com/a") == "https://x.com/a"


# ---- robots.txt: unreachable/missing → allow-all ---------------------------


def test_robots_unreachable_defaults_to_allow_all():
    """When robots.txt raises WebError the crawler falls back to allow-all so a
    missing robots file doesn't silently block an explicit crawl."""

    def _fetch(url: str):
        if url.endswith("/robots.txt"):
            raise WebError("connection refused")
        return ("text/html", "<p>page</p>")

    # depth=0 so only the start URL is fetched; if robots defaulted to *deny*
    # we'd get zero documents.
    docs = load_web_crawl("https://x.com/", _crawl_cfg(depth="0"), fetch=_fetch)
    assert len(docs) == 1
    assert docs[0].source == "https://x.com/"


# ---- sitemap mode: max_pages truncation warning -----------------------------


def test_sitemap_mode_warns_on_max_pages_truncation():
    """load_web_sitemap emits a UserWarning (not a silent cut) when the page cap is hit."""
    big = "".join(f"<url><loc>https://docs.x.com/p{i}</loc></url>" for i in range(20))
    xml = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + big + "</urlset>"
    pages: dict = {"https://docs.x.com/sitemap.xml": ("application/xml", xml)}
    for i in range(20):
        pages[f"https://docs.x.com/p{i}"] = "<p>text</p>"
    web = _FakeWeb(pages=pages)
    with pytest.warns(UserWarning, match="max_pages"):
        docs = load_web_sitemap(
            "https://docs.x.com/sitemap.xml",
            _sitemap_cfg(max_pages="5"),
            fetch=web.fetch,
        )
    assert len(docs) == 5


# ---- politeness delay -------------------------------------------------------


def test_crawl_delay_is_invoked(monkeypatch):
    """_sleep must be called once per fetched page so the polite-crawl contract holds."""
    sleep_calls: list[float] = []
    monkeypatch.setattr("corpuslint.sources.web._sleep", lambda d: sleep_calls.append(d))
    web = _FakeWeb(pages={"https://x.com/": "<p>home</p>"})
    # depth=0: one page fetched → one sleep
    load_web_crawl("https://x.com/", _crawl_cfg(depth="0", delay="0.1"), fetch=web.fetch)
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(0.1)


def test_sitemap_mode_delay_is_invoked(monkeypatch):
    """_sleep must be called once per page in sitemap mode."""
    sleep_calls: list[float] = []
    monkeypatch.setattr("corpuslint.sources.web._sleep", lambda d: sleep_calls.append(d))
    web = _FakeWeb(
        pages={
            "https://docs.x.com/sitemap.xml": ("application/xml", _URLSET),
            "https://docs.x.com/a": "<p>one</p>",
            "https://docs.x.com/b": "<p>two</p>",
        }
    )
    load_web_sitemap("https://docs.x.com/sitemap.xml", _sitemap_cfg(delay="0.3"), fetch=web.fetch)
    assert len(sleep_calls) == 2
    assert all(d == pytest.approx(0.3) for d in sleep_calls)


# ---- sitemap mode: nested index end-to-end ----------------------------------


def test_sitemap_mode_nested_index_fetches_all_pages():
    """load_web_sitemap follows a sitemapindex all the way through to page Documents."""
    web = _FakeWeb(
        pages={
            "https://docs.x.com/sitemap.xml": ("application/xml", _INDEX),
            "https://docs.x.com/sitemap-1.xml": ("application/xml", _URLSET),
            "https://docs.x.com/sitemap-2.xml": (
                "application/xml",
                _URLSET.replace("/a", "/c").replace("/b", "/d"),
            ),
            "https://docs.x.com/a": "<p>alpha</p>",
            "https://docs.x.com/b": "<p>bravo</p>",
            "https://docs.x.com/c": "<p>charlie</p>",
            "https://docs.x.com/d": "<p>delta</p>",
        }
    )
    docs = load_web_sitemap("https://docs.x.com/sitemap.xml", _sitemap_cfg(), fetch=web.fetch)
    assert {d.source for d in docs} == {
        "https://docs.x.com/a",
        "https://docs.x.com/b",
        "https://docs.x.com/c",
        "https://docs.x.com/d",
    }


# ---- HTTP layer -------------------------------------------------------------


def _make_http_error(code: int, msg: str = "Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://docs.x.com/a", code=code, msg=msg, hdrs={}, fp=io.BytesIO(b"")  # type: ignore[arg-type]
    )


def test_http_fetch_sends_user_agent_and_returns_type_and_body(monkeypatch):
    captured: dict = {}

    class _Resp:
        headers = type(
            "H",
            (),
            {
                "get_content_type": lambda self: "text/html",
                "get_content_charset": lambda self: "utf-8",
            },
        )()

        def read(self):
            return b"<p>hi</p>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(request, timeout=None):
        captured["ua"] = request.get_header("User-agent")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    content_type, body = _http_fetch("https://docs.x.com/a")
    assert content_type == "text/html"
    assert body == "<p>hi</p>"
    assert captured["ua"].startswith("corpuslint/")


def test_http_fetch_http_error_raises_web_error(monkeypatch):
    def _boom(req, timeout=None):
        raise _make_http_error(404, "Not Found")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(WebError) as exc:
        _http_fetch("https://docs.x.com/a")
    assert "404" in str(exc.value)


def test_http_fetch_url_error_raises_web_error(monkeypatch):
    def _boom(req, timeout=None):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(WebError):
        _http_fetch("https://docs.x.com/a")
