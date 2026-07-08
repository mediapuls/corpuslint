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


def test_mcp_extra_declares_mcp_sdk():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert "mcp" in extras
    assert any(dep.startswith("mcp") for dep in extras["mcp"])


def test_mcp_console_script_registered():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["corpuslint-mcp"] == "corpuslint.mcp_server:main"
