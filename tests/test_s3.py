import sys
import types

import pytest

from corpuslint.config import Config
from corpuslint.models import Document
from corpuslint.sources.base import SourceError


def _cfg(**over) -> Config:
    cfg = Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


# ---- fakes: the only mocked seam is the boto3 S3 client ---------------------


class _FakeBody:
    """Mimics the StreamingBody returned under get_object()['Body']."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakePaginator:
    """Mimics client.get_paginator('list_objects_v2').

    ``.paginate(**kwargs)`` records the kwargs and yields the canned pages so a
    test can assert Bucket/Prefix were forwarded and that every page is walked.
    """

    def __init__(self, pages, on_paginate=None):
        self._pages = pages
        self._on_paginate = on_paginate
        self.paginate_kwargs: dict | None = None

    def paginate(self, **kwargs):
        self.paginate_kwargs = kwargs
        if self._on_paginate is not None:
            self._on_paginate()
        return iter(self._pages)


class _FakeS3Client:
    """A canned S3 client: objects is {key: bytes}. ``get_error`` maps a key to
    an exception raised on get_object (per-object failure). ``list_error``, if
    set, is raised while paginating (credential / access failure)."""

    def __init__(self, objects=None, *, page_size=1000, get_error=None, list_error=None, **init_kwargs):
        self.objects = objects or {}
        self.page_size = page_size
        self.get_error = get_error or {}
        self.list_error = list_error
        self.init_kwargs = init_kwargs
        self.got_keys: list[str] = []
        self.paginators: list[_FakePaginator] = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        keys = list(self.objects)
        pages = []
        for i in range(0, len(keys), self.page_size) or [0]:
            chunk = keys[i : i + self.page_size]
            page = {}
            if chunk:
                page["Contents"] = [{"Key": k} for k in chunk]
            pages.append(page)
        if not keys:
            pages = [{}]  # an empty bucket still yields one (contents-less) page

        def _maybe_raise():
            if self.list_error is not None:
                raise self.list_error

        p = _FakePaginator(pages, on_paginate=_maybe_raise)
        self.paginators.append(p)
        return p

    def get_object(self, *, Bucket, Key):
        self.got_keys.append(Key)
        if Key in self.get_error:
            raise self.get_error[Key]
        return {"Body": _FakeBody(self.objects[Key])}


# ---- module-under-test import (lazy boto3 => import must always work) --------

from corpuslint.sources.s3 import (  # noqa: E402
    S3Error,
    S3Source,
    _documents_from_client,
    _list_keys,
    load_s3_documents,
)


# ---- enumeration / pagination (inject client directly) ----------------------


def test_list_keys_walks_every_page():
    objects = {f"docs/{i}.md": b"x" for i in range(5)}
    client = _FakeS3Client(objects, page_size=2)  # 5 keys => 3 pages
    keys = _list_keys(client, "bkt", "docs/")
    assert sorted(keys) == sorted(objects)
    # Prefix + Bucket forwarded to the paginator (no silent cap).
    assert client.paginators[-1].paginate_kwargs == {"Bucket": "bkt", "Prefix": "docs/"}


def test_list_keys_omits_prefix_when_empty():
    client = _FakeS3Client({"a.md": b"x"})
    _list_keys(client, "bkt", "")
    assert client.paginators[-1].paginate_kwargs == {"Bucket": "bkt"}


# ---- extension filtering + download + parse + source rebrand ----------------


def test_supported_text_and_html_objects_are_parsed_binaries_skipped():
    objects = {
        "a.md": b"# Title\n\nMarkdown body.",
        "b.txt": b"plain text body",
        "c.html": b"<h1>Head</h1><p>Html body</p>",
        "d.htm": b"<p>Also html</p>",
        "skip.png": b"\x89PNG binary",
        "skip.zip": b"PK\x03\x04binary",
        # PDF is skipped: the file loader has no PDF parser, and this connector
        # reuses it rather than reinventing extraction.
        "skip.pdf": b"%PDF-1.7 binary",
        "nested/e.md": b"nested markdown",
        "folder/": b"",  # key with no extension (folder placeholder)
    }
    client = _FakeS3Client(objects)
    docs = _documents_from_client(client, "bkt", "", _cfg())
    sources = {d.source for d in docs}
    assert sources == {
        "s3://bkt/a.md",
        "s3://bkt/b.txt",
        "s3://bkt/c.html",
        "s3://bkt/d.htm",
        "s3://bkt/nested/e.md",
    }
    # binaries were never downloaded (filtered before get_object)
    assert "skip.png" not in client.got_keys
    assert "skip.zip" not in client.got_keys
    assert "skip.pdf" not in client.got_keys


def test_html_object_is_run_through_the_html_extractor():
    objects = {"page.html": b"<h1>Refund policy</h1><p>Refunds take 5 days.</p>"}
    client = _FakeS3Client(objects)
    docs = _documents_from_client(client, "bkt", "", _cfg())
    assert docs[0].source == "s3://bkt/page.html"
    # tags stripped by the shared html_to_text extractor
    assert docs[0].text == "Refund policy Refunds take 5 days."
    assert "<h1>" not in docs[0].text


def test_markdown_object_maps_to_document_with_s3_source():
    client = _FakeS3Client({"guide.md": b"install the widget"})
    docs = _documents_from_client(client, "my-bucket", "", _cfg())
    assert docs == [Document(text="install the widget", source="s3://my-bucket/guide.md")]


def test_per_object_download_error_is_skipped_with_warning_others_kept():
    objects = {"ok1.md": b"first", "boom.md": b"never", "ok2.md": b"second"}
    client = _FakeS3Client(objects, get_error={"boom.md": OSError("connection reset")})
    with pytest.warns(UserWarning, match="boom.md"):
        docs = _documents_from_client(client, "bkt", "", _cfg())
    assert {d.text for d in docs} == {"first", "second"}


def test_per_object_warning_does_not_leak_secret_from_error_text():
    # Even if a backend error carried a secret, the warning must not echo it.
    objects = {"x.md": b"body"}
    client = _FakeS3Client(objects, get_error={"x.md": OSError("token=SECRET-LEAK-123")})
    with pytest.warns(UserWarning) as record:
        _documents_from_client(client, "bkt", "", _cfg())
    assert not any("SECRET-LEAK-123" in str(w.message) for w in record)


# ---- load_s3_documents: boto3 wiring (fake SDK) -----------------------------


def _install_fake_boto3(monkeypatch, objects=None, **client_kwargs):
    """Inject a fake boto3 + botocore.exceptions so tests never need the real SDK."""
    created: dict = {}

    class _NoCredentialsError(Exception):
        pass

    class _BotoCoreError(Exception):
        pass

    class _ClientError(Exception):
        pass

    def _client(service, **kwargs):
        assert service == "s3"
        c = _FakeS3Client(objects, **{**client_kwargs, **kwargs})
        created["client"] = c
        created["client_kwargs"] = kwargs
        return c

    boto3_mod = types.ModuleType("boto3")
    boto3_mod.client = _client
    exc_mod = types.ModuleType("botocore.exceptions")
    exc_mod.NoCredentialsError = _NoCredentialsError
    exc_mod.BotoCoreError = _BotoCoreError
    exc_mod.ClientError = _ClientError
    botocore_mod = types.ModuleType("botocore")
    monkeypatch.setitem(sys.modules, "boto3", boto3_mod)
    monkeypatch.setitem(sys.modules, "botocore", botocore_mod)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exc_mod)
    created["errors"] = types.SimpleNamespace(
        NoCredentialsError=_NoCredentialsError,
        BotoCoreError=_BotoCoreError,
        ClientError=_ClientError,
    )
    return created


def test_load_builds_client_and_returns_documents(monkeypatch, capsys):
    _install_fake_boto3(monkeypatch, objects={"a.md": b"hello world"})
    docs = load_s3_documents("bkt", "", _cfg())
    assert docs == [Document(text="hello world", source="s3://bkt/a.md")]
    # count reported on stderr, stdout stays clean (for --json)
    captured = capsys.readouterr()
    assert "fetched 1 documents" in captured.err
    assert captured.out == ""


def test_load_passes_endpoint_url_and_region_for_s3_compatible_stores(monkeypatch):
    created = _install_fake_boto3(monkeypatch, objects={"a.md": b"x"})
    load_s3_documents(
        "bkt", "", _cfg(), endpoint_url="https://r2.cloudflarestorage.com", region="auto"
    )
    assert created["client_kwargs"]["endpoint_url"] == "https://r2.cloudflarestorage.com"
    assert created["client_kwargs"]["region_name"] == "auto"


def test_load_omits_endpoint_and_region_when_not_given(monkeypatch):
    created = _install_fake_boto3(monkeypatch, objects={"a.md": b"x"})
    load_s3_documents("bkt", "", _cfg())
    assert "endpoint_url" not in created["client_kwargs"]
    assert "region_name" not in created["client_kwargs"]


def test_missing_boto3_extra_raises_actionable_install_hint(monkeypatch):
    # Force `import boto3` to fail.
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(S3Error) as exc:
        load_s3_documents("bkt", "", _cfg())
    assert "corpuslint[s3]" in str(exc.value)


def test_credential_resolution_failure_is_clean_and_leaks_no_secret(monkeypatch):
    created = _install_fake_boto3(monkeypatch, objects={"a.md": b"x"})
    no_creds = created["errors"].NoCredentialsError("using AWS_SECRET_ACCESS_KEY=super-secret")

    # Make listing raise as if boto3 could not resolve credentials.
    real_client = sys.modules["boto3"].client

    def _client(service, **kwargs):
        c = real_client(service, **kwargs)
        c.list_error = no_creds
        return c

    monkeypatch.setattr(sys.modules["boto3"], "client", _client)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret")
    with pytest.raises(S3Error) as exc:
        load_s3_documents("bkt", "", _cfg())
    msg = str(exc.value)
    assert "credential" in msg.lower()
    assert "super-secret" not in msg


def test_base_import_unaffected_when_boto3_absent(monkeypatch):
    """Importing the module must not require boto3 at module level (lazy import)."""
    monkeypatch.delitem(sys.modules, "corpuslint.sources.s3", raising=False)
    monkeypatch.setitem(sys.modules, "boto3", None)
    import corpuslint.sources.s3 as mod

    assert callable(mod.load_s3_documents)
    assert issubclass(mod.S3Error, RuntimeError)


# ---- S3Source: options + registry -------------------------------------------


def test_source_requires_bucket():
    with pytest.raises(SourceError, match="bucket"):
        S3Source().load(_cfg())


def test_source_requires_bucket_even_with_other_opts():
    with pytest.raises(SourceError, match="bucket"):
        S3Source().load(_cfg(source_options={"prefix": "docs/"}))


def test_source_reads_bucket_prefix_endpoint_region_from_options(monkeypatch):
    created = _install_fake_boto3(monkeypatch, objects={"docs/a.md": b"hi"})
    cfg = _cfg(
        source_options={
            "bucket": "bkt",
            "prefix": "docs/",
            "endpoint_url": "https://minio.local",
            "region": "us-east-1",
        }
    )
    docs = S3Source().load(cfg)
    assert docs == [Document(text="hi", source="s3://bkt/docs/a.md")]
    assert created["client_kwargs"]["endpoint_url"] == "https://minio.local"
    assert created["client_kwargs"]["region_name"] == "us-east-1"
    assert created["client"].paginators[-1].paginate_kwargs["Prefix"] == "docs/"


def test_source_registered_under_s3():
    from corpuslint.sources.base import get_source

    assert get_source("s3").name == "s3"
