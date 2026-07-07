import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_llm_extra_declares_openai():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert "llm" in extras
    assert any(dep.startswith("openai") for dep in extras["llm"])


def test_azure_extra_declares_search_documents():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert "azure" in extras
    assert any(dep.startswith("azure-search-documents") for dep in extras["azure"])
