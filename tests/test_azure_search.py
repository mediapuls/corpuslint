import sys
import types

import pytest

from corpuslint.config import Config
from corpuslint.models import Document
from corpuslint.sources.azure_search import (
    AzureSearchError,
    _documents_from_client,
    load_azure_documents,
)


class _FakePaged:
    """Mimics azure SearchItemPaged: .by_page() yields one iterator per page."""

    def __init__(self, pages: list[list[dict]]):
        self._pages = pages

    def by_page(self):
        return iter([iter(page) for page in self._pages])


class _FakeSearchClient:
    instances: list = []

    def __init__(self, pages: list[list[dict]] | None = None, **kwargs):
        self.pages = pages or []
        self.search_kwargs: dict | None = None
        self.init_kwargs = kwargs
        _FakeSearchClient.instances.append(self)

    def search(self, **kwargs):
        self.search_kwargs = kwargs
        return _FakePaged(self.pages)


def _cfg(**over) -> Config:
    cfg = Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


# ---- pagination & mapping (inject client directly) --------------------------


def test_paginates_over_all_pages_dropping_nothing():
    pages = [
        [{"id": "1", "content": "one"}, {"id": "2", "content": "two"}],
        [{"id": "3", "content": "three"}],
        [{"id": "4", "content": "four"}, {"id": "5", "content": "five"}],
    ]
    client = _FakeSearchClient(pages)
    docs = _documents_from_client(client, "myindex", _cfg())
    assert [d.text for d in docs] == ["one", "two", "three", "four", "five"]
    assert client.search_kwargs["search_text"] == "*"


def test_maps_content_and_id_from_configured_fields():
    pages = [[{"key": "abc", "body": "hello world"}]]
    client = _FakeSearchClient(pages)
    docs = _documents_from_client(client, "kb", _cfg(content_field="body", id_field="key"))
    assert docs == [Document(text="hello world", source="azure-search://kb/abc")]


def test_missing_content_field_skips_with_warning_keeps_others():
    pages = [[{"id": "1", "content": "kept"}, {"id": "2"}, {"id": "3", "content": "also kept"}]]
    client = _FakeSearchClient(pages)
    with pytest.warns(UserWarning, match="content"):
        docs = _documents_from_client(client, "kb", _cfg())
    assert [d.text for d in docs] == ["kept", "also kept"]


def test_falls_back_to_search_document_key_when_id_field_absent():
    pages = [[{"content": "x", "@search.documentKey": "k9"}]]
    client = _FakeSearchClient(pages)
    docs = _documents_from_client(client, "kb", _cfg(id_field="missing"))
    assert docs[0].source == "azure-search://kb/k9"


# ---- credentials / SDK wiring (load_azure_documents) ------------------------


def _install_fake_sdk(monkeypatch, pages: list[list[dict]] | None = None):
    _FakeSearchClient.instances = []

    def _client_factory(*, endpoint, index_name, credential):
        c = _FakeSearchClient(pages, endpoint=endpoint, index_name=index_name, credential=credential)
        return c

    search_mod = types.ModuleType("azure.search.documents")
    search_mod.SearchClient = _client_factory
    cred_mod = types.ModuleType("azure.core.credentials")
    cred_mod.AzureKeyCredential = lambda key: {"key": key}
    monkeypatch.setitem(sys.modules, "azure.search.documents", search_mod)
    monkeypatch.setitem(sys.modules, "azure.core.credentials", cred_mod)


def test_load_reads_env_and_searches_wildcard(monkeypatch):
    pages = [[{"id": "1", "content": "hi"}]]
    _install_fake_sdk(monkeypatch, pages)
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://svc.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "secret-key")
    docs = load_azure_documents("kb", _cfg())
    assert [d.text for d in docs] == ["hi"]
    client = _FakeSearchClient.instances[-1]
    assert client.init_kwargs["endpoint"] == "https://svc.search.windows.net"
    assert client.init_kwargs["index_name"] == "kb"
    assert client.init_kwargs["credential"] == {"key": "secret-key"}


def test_missing_extra_raises_install_hint(monkeypatch):
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://svc.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "secret-key")
    # Force `from azure.search.documents import SearchClient` to fail.
    monkeypatch.setitem(sys.modules, "azure.search.documents", None)
    with pytest.raises(AzureSearchError) as exc:
        load_azure_documents("kb", _cfg())
    assert "corpuslint[azure]" in str(exc.value)


def test_missing_endpoint_env_raises(monkeypatch):
    _install_fake_sdk(monkeypatch)
    monkeypatch.delenv("AZURE_SEARCH_ENDPOINT", raising=False)
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "secret-key")
    with pytest.raises(AzureSearchError) as exc:
        load_azure_documents("kb", _cfg())
    assert "AZURE_SEARCH_ENDPOINT" in str(exc.value)


def test_missing_api_key_env_raises(monkeypatch):
    _install_fake_sdk(monkeypatch)
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://svc.search.windows.net")
    monkeypatch.delenv("AZURE_SEARCH_API_KEY", raising=False)
    with pytest.raises(AzureSearchError) as exc:
        load_azure_documents("kb", _cfg())
    assert "AZURE_SEARCH_API_KEY" in str(exc.value)


def test_error_does_not_leak_api_key_value(monkeypatch):
    _install_fake_sdk(monkeypatch)
    monkeypatch.delenv("AZURE_SEARCH_ENDPOINT", raising=False)
    monkeypatch.setenv("AZURE_SEARCH_API_KEY", "super-secret-value")
    with pytest.raises(AzureSearchError) as exc:
        load_azure_documents("kb", _cfg())
    assert "super-secret-value" not in str(exc.value)


# ---- base install isolation -------------------------------------------------


def test_base_import_unaffected_when_azure_sdk_absent(monkeypatch):
    """Importing corpuslint must not require azure-search-documents at module level.

    The SDK is imported lazily inside _import_sdk(); top-level module import
    must succeed even when the azure extra is not installed.
    """
    # Drop the cached module so Python re-executes it from source.
    monkeypatch.delitem(sys.modules, "corpuslint.sources.azure_search", raising=False)
    # Simulate the azure packages being absent.
    monkeypatch.setitem(sys.modules, "azure.search.documents", None)
    monkeypatch.setitem(sys.modules, "azure.core.credentials", None)

    # Re-importing the module must not raise.
    import corpuslint.sources.azure_search as mod

    assert callable(mod.load_azure_documents)
    assert issubclass(mod.AzureSearchError, RuntimeError)
