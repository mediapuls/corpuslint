import sys
import types

import pytest

from corpuslint.llm_clients import LLMClientError, get_llm_client


class _FakeCompletions:
    def __init__(self, parent):
        self.parent = parent

    def create(self, **kwargs):
        self.parent.calls.append(kwargs)
        msg = types.SimpleNamespace(content="YES")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


class _FakeChat:
    def __init__(self, parent):
        self.completions = _FakeCompletions(parent)


class _FakeClientBase:
    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.calls = []
        self.chat = _FakeChat(self)


class _FakeOpenAI(_FakeClientBase):
    instances: list = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        _FakeOpenAI.instances.append(self)


class _FakeAzureOpenAI(_FakeClientBase):
    instances: list = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        _FakeAzureOpenAI.instances.append(self)


def _install_fake_openai(monkeypatch):
    _FakeOpenAI.instances = []
    _FakeAzureOpenAI.instances = []
    mod = types.ModuleType("openai")
    mod.OpenAI = _FakeOpenAI
    mod.AzureOpenAI = _FakeAzureOpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    return mod


def test_import_error_gives_install_hint(monkeypatch):
    # Force `import openai` to raise ImportError regardless of the environment.
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(LLMClientError) as exc:
        get_llm_client("openai", "")
    assert "corpuslint[llm]" in str(exc.value)


def test_openai_selected_with_default_model(monkeypatch):
    _install_fake_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = get_llm_client("openai", "")
    assert len(_FakeOpenAI.instances) == 1
    assert _FakeAzureOpenAI.instances == []
    answer = client.complete("hello")
    assert answer == "YES"
    call = _FakeOpenAI.instances[0].calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert call["temperature"] == 0


def test_openai_missing_key_raises(monkeypatch):
    _install_fake_openai(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMClientError) as exc:
        get_llm_client("openai", "")
    assert "OPENAI_API_KEY" in str(exc.value)


def test_azure_uses_deployment_model_and_reads_env(monkeypatch):
    _install_fake_openai(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    client = get_llm_client("azure", "my-deployment")
    assert len(_FakeAzureOpenAI.instances) == 1
    assert _FakeOpenAI.instances == []
    client.complete("hi")
    call = _FakeAzureOpenAI.instances[0].calls[0]
    assert call["model"] == "my-deployment"
    assert call["temperature"] == 0


def test_azure_missing_endpoint_raises(monkeypatch):
    _install_fake_openai(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key")
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(LLMClientError) as exc:
        get_llm_client("azure", "my-deployment")
    assert "AZURE_OPENAI_ENDPOINT" in str(exc.value)


def test_unknown_provider_raises(monkeypatch):
    _install_fake_openai(monkeypatch)
    with pytest.raises(LLMClientError) as exc:
        get_llm_client("cohere", "")
    assert "cohere" in str(exc.value)
