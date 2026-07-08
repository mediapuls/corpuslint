from __future__ import annotations

import importlib.metadata
import sys
import time
import urllib.error
import urllib.request
import warnings
import xml.etree.ElementTree as ET
from collections import deque
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

from ..config import Config
from ..loader import html_to_text
from ..models import Document
from .base import SourceError, register

# A crawler must identify itself. Version comes from the installed package
# metadata so the UA tracks releases without a second source of truth.
try:
    _VERSION = importlib.metadata.version("corpuslint")
except importlib.metadata.PackageNotFoundError:  # pragma: no cover - source checkout
    _VERSION = "0"
_USER_AGENT = f"corpuslint/{_VERSION} (+https://github.com/mediapuls/corpuslint)"

# Only these content types carry prose we can lint; everything else (PDF, images,
# JSON APIs) is skipped so we never feed binary junk through html_to_text.
_HTML_TYPES = {"text/html", "application/xhtml+xml"}

# Guardrail defaults. A crawler must be bounded and polite by default.
_DEFAULT_DEPTH = 2
_DEFAULT_MAX_PAGES = 200
_DEFAULT_DELAY = 0.5  # seconds between requests, so we don't hammer a site
_TIMEOUT = 20  # per-request socket timeout
# Cap how many sitemap files a (possibly self-referential) index can pull in.
_MAX_SITEMAPS = 50


class WebError(SourceError):
    """Raised when the web source cannot run (missing options, HTTP failure)."""


def _http_fetch(url: str) -> tuple[str, str]:
    """GET ``url`` with our User-Agent; return ``(content_type, body_text)``.

    Standard library only, so the web source needs no optional extra. The
    content type is the bare MIME (no charset params); the body is decoded with
    the response charset. Network/HTTP failures are wrapped in WebError so the
    caller can skip a single page without a traceback.
    """
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="ignore")
            return content_type, body
    except urllib.error.HTTPError as e:
        raise WebError(f"HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        # e.reason may name the host; it never carries request headers.
        raise WebError(f"could not reach {url}: {e.reason}") from e


Fetch = Callable[[str], tuple[str, str]]


def _strip_fragment(url: str) -> str:
    """Drop the ``#fragment`` so ``/a`` and ``/a#top`` dedupe to one page."""
    return urlunparse(urlparse(url)._replace(fragment=""))


def _localname(tag: str) -> str:
    """Tag name without its ``{namespace}`` prefix (sitemaps are namespaced)."""
    return tag.rsplit("}", 1)[-1]


def _parse_sitemap(xml_text: str) -> tuple[list[str], bool]:
    """Parse a sitemap; return ``(locs, is_index)``.

    A ``<sitemapindex>`` yields nested sitemap URLs (``is_index`` True); a
    ``<urlset>`` yields page URLs. Works regardless of the XML namespace by
    matching on local tag names. Malformed XML raises WebError.

    Sitemaps come from the open web, so this guards the stdlib parser (which is
    stdlib-only by design — no defusedxml extra) against entity-expansion /
    XXE payloads: a real sitemap has no DOCTYPE, so any ``<!DOCTYPE`` is refused
    before parsing rather than expanded.
    """
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise WebError("sitemap contains a DOCTYPE/entity declaration; refusing to parse")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise WebError(f"invalid sitemap XML: {e}") from e
    is_index = _localname(root.tag) == "sitemapindex"
    locs: list[str] = []
    for child in root:
        for sub in child:
            if _localname(sub.tag) == "loc" and sub.text and sub.text.strip():
                locs.append(sub.text.strip())
    return locs, is_index


def _collect_sitemap_urls(
    sitemap_url: str, fetch: Fetch, max_pages: int, delay: float = 0.0
) -> tuple[list[str], bool]:
    """BFS a sitemap (following nested indexes) into a deduped page-URL list.

    Bounded on two axes so a pathological/self-referential index can't run away:
    at most ``_MAX_SITEMAPS`` sitemap files, and at most ``max_pages`` page URLs.
    Returns ``(urls, truncated)`` where ``truncated`` flags that a cap was hit.
    A sitemap that fails to fetch/parse is skipped with a warning, not fatal.
    ``delay`` seconds are slept after each sitemap-file fetch so index sub-files
    aren't pulled back-to-back.
    """
    page_urls: list[str] = []
    seen_pages: set[str] = set()
    seen_sitemaps: set[str] = set()
    queue: deque[str] = deque([sitemap_url])
    fetches = 0
    while queue:
        sm = queue.popleft()
        if sm in seen_sitemaps:
            continue
        seen_sitemaps.add(sm)
        if len(seen_sitemaps) > _MAX_SITEMAPS:
            return page_urls, True
        if fetches > 0:
            _sleep(delay)  # polite gap between sitemap-file fetches
        fetches += 1
        try:
            _, body = fetch(sm)
            locs, is_index = _parse_sitemap(body)
        except WebError as e:
            warnings.warn(f"skipping sitemap {sm}: {e}", UserWarning, stacklevel=2)
            continue
        if is_index:
            queue.extend(locs)
            continue
        for loc in locs:
            loc = _strip_fragment(loc)
            if loc in seen_pages:
                continue
            seen_pages.add(loc)
            page_urls.append(loc)
            if len(page_urls) >= max_pages:
                return page_urls, True
    return page_urls, False


class _LinkExtractor(HTMLParser):
    """Collect the raw ``href`` of every ``<a>`` in a page."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for key, value in attrs:
            if key == "href" and value:
                self.hrefs.append(value)


def _extract_links(html: str, base_url: str) -> list[str]:
    """Absolute http(s) links from ``html``, resolved against ``base_url``.

    Relative hrefs are joined to the page URL, fragments are stripped, and
    non-http(s) schemes (mailto:, tel:, javascript:) are dropped.
    """
    parser = _LinkExtractor()
    parser.feed(html)
    links: list[str] = []
    for href in parser.hrefs:
        absolute = urljoin(base_url, href)
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        links.append(_strip_fragment(absolute))
    return links


class _Robots:
    """Per-host robots.txt gate, caching one RobotFileParser per origin.

    Robots files are fetched through the same (mockable) ``fetch`` seam as pages.
    If a site's robots.txt can't be fetched, we default to *allow* — the standard
    permissive fallback — so a missing file doesn't block an explicit crawl.
    """

    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch
        self._cache: dict[str, RobotFileParser] = {}

    def _parser(self, url: str) -> RobotFileParser:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._cache.get(origin)
        if rp is None:
            rp = self._load(origin)
            self._cache[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        return self._parser(url).can_fetch(_USER_AGENT, url)

    def crawl_delay(self, url: str) -> float | None:
        """robots.txt Crawl-delay for ``url``'s host, or None if unspecified."""
        cd = self._parser(url).crawl_delay(_USER_AGENT)
        return float(cd) if cd is not None else None

    def _load(self, origin: str) -> RobotFileParser:
        rp = RobotFileParser()
        try:
            _, body = self._fetch(f"{origin}/robots.txt")
        except WebError:
            rp.parse([])  # allow all
            return rp
        rp.parse(body.splitlines())
        return rp


def _fetch_document(url: str, fetch: Fetch) -> Document | None:
    """Fetch one URL into a Document, or None if it should be skipped.

    A fetch error or a non-HTML content type warns and returns None so a single
    bad page never aborts the whole run.
    """
    try:
        content_type, body = fetch(url)
    except WebError as e:
        warnings.warn(f"skipping {url}: {e}", UserWarning, stacklevel=2)
        return None
    if content_type not in _HTML_TYPES:
        warnings.warn(
            f"skipping {url}: non-HTML content-type {content_type!r}", UserWarning, stacklevel=2
        )
        return None
    return Document(text=html_to_text(body), source=url)


def _sleep(delay: float) -> None:
    if delay > 0:
        time.sleep(delay)


def _effective_delay(base_delay: float, robots: "_Robots", url: str) -> float:
    """Politeness delay for ``url``: the larger of our default and robots Crawl-delay."""
    crawl_delay = robots.crawl_delay(url)
    return max(base_delay, crawl_delay) if crawl_delay is not None else base_delay


def _int_opt(opts: dict, key: str, default: int) -> int:
    raw = opts.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as e:
        raise WebError(f"--source-opt {key}={raw!r} must be an integer") from e


def _float_opt(opts: dict, key: str, default: float) -> float:
    raw = opts.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as e:
        raise WebError(f"--source-opt {key}={raw!r} must be a number") from e


def load_web_sitemap(
    sitemap_url: str, config: Config, fetch: Fetch | None = None
) -> list[Document]:
    """Fetch every page listed in a sitemap (bounded) into Documents."""
    fetch = fetch or _http_fetch
    opts = config.source_options
    max_pages = _int_opt(opts, "max_pages", _DEFAULT_MAX_PAGES)
    delay = _float_opt(opts, "delay", _DEFAULT_DELAY)

    robots = _Robots(fetch)
    urls, truncated = _collect_sitemap_urls(
        sitemap_url, fetch, max_pages, delay=_effective_delay(delay, robots, sitemap_url)
    )
    if truncated:
        warnings.warn(
            f"sitemap truncated at max_pages={max_pages}; not all pages were fetched",
            UserWarning,
            stacklevel=2,
        )
    docs: list[Document] = []
    for url in urls:
        if not robots.allowed(url):
            warnings.warn(f"skipping {url}: disallowed by robots.txt", UserWarning, stacklevel=2)
            continue
        doc = _fetch_document(url, fetch)
        if doc is not None:
            docs.append(doc)
        _sleep(_effective_delay(delay, robots, url))
    # Report on stderr (keeps stdout/--json clean) to back the "no silent cap" claim.
    print(f"fetched {len(docs)} documents from sitemap {sitemap_url!r}", file=sys.stderr)
    return docs


def load_web_crawl(
    start_url: str, config: Config, fetch: Fetch | None = None
) -> list[Document]:
    """BFS-crawl same-domain pages from ``start_url`` into Documents.

    Bounded by ``depth`` and ``max_pages``; polite via a per-request ``delay`` and
    robots.txt; stays on the start URL's domain; dedupes visited URLs. Per-page
    fetch errors and non-HTML pages are skipped with a warning, never fatal.
    """
    fetch = fetch or _http_fetch
    opts = config.source_options
    depth = _int_opt(opts, "depth", _DEFAULT_DEPTH)
    max_pages = _int_opt(opts, "max_pages", _DEFAULT_MAX_PAGES)
    delay = _float_opt(opts, "delay", _DEFAULT_DELAY)

    start_url = _strip_fragment(start_url)
    domain = urlparse(start_url).netloc
    robots = _Robots(fetch)

    docs: list[Document] = []
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    truncated = False

    while queue:
        url, url_depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        if len(docs) >= max_pages:
            truncated = True
            break
        if not robots.allowed(url):
            warnings.warn(f"skipping {url}: disallowed by robots.txt", UserWarning, stacklevel=2)
            continue
        # We're about to hit the network, so sleep after the attempt no matter how
        # it turns out — a page of 404s/PDFs must not become rapid-fire requests.
        # robots.txt Crawl-delay, if any, raises the effective delay.
        try:
            try:
                content_type, body = fetch(url)
            except WebError as e:
                warnings.warn(f"skipping {url}: {e}", UserWarning, stacklevel=2)
                continue
            if content_type not in _HTML_TYPES:
                warnings.warn(
                    f"skipping {url}: non-HTML content-type {content_type!r}",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            docs.append(Document(text=html_to_text(body), source=url))
            if url_depth < depth:
                for link in _extract_links(body, url):
                    if urlparse(link).netloc != domain:  # same-domain only
                        continue
                    if link not in visited:
                        queue.append((link, url_depth + 1))
        finally:
            _sleep(_effective_delay(delay, robots, url))

    if truncated:
        warnings.warn(
            f"crawl truncated at max_pages={max_pages}; not all pages were fetched",
            UserWarning,
            stacklevel=2,
        )
    print(f"fetched {len(docs)} documents from crawl of {start_url!r}", file=sys.stderr)
    return docs


class WebSource:
    name = "web"

    def load(self, config: Config) -> list[Document]:
        # Two modes, both driven by source_options. sitemap wins if both are set.
        opts = config.source_options
        sitemap = opts.get("sitemap")
        url = opts.get("url")
        if sitemap:
            return load_web_sitemap(sitemap, config)
        if url:
            return load_web_crawl(url, config)
        raise SourceError(
            "the web source requires a starting point "
            "(pass --source-opt sitemap=<sitemap-url> or --source-opt url=<start-url>)"
        )


register(WebSource())
