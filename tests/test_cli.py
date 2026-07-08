from pathlib import Path
from typer.testing import CliRunner
from corpuslint.cli import app
from corpuslint.llm_clients import LLMClientError
from corpuslint.models import Document
from corpuslint.sources.azure_search import AzureSearchError

runner = CliRunner()


class _StubLLM:
    def __init__(self, answer: str = "YES"):
        self.answer = answer

    def complete(self, prompt: str) -> str:
        return self.answer


class _AllSameEmbedder:
    """Every text maps to the same vector, so any pair passes the similarity prefilter."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def test_cli_llm_backend_error_is_clean(tmp_path: Path, monkeypatch):
    (tmp_path / "a.md").write_text("hello world")
    monkeypatch.setattr(
        "corpuslint.cli.get_llm_client",
        lambda provider, model: (_ for _ in ()).throw(
            LLMClientError("OPENAI_API_KEY is not set.")
        ),
    )
    result = runner.invoke(app, [str(tmp_path), "--llm"])
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output
    assert "Traceback" not in result.output
    # a missing env var is a config error, not a bad CLI argument
    assert "Invalid value" not in result.output


def test_cli_invalid_provider_is_clean(tmp_path: Path):
    (tmp_path / "a.md").write_text("hello world")
    result = runner.invoke(app, [str(tmp_path), "--llm", "--llm-provider", "cohere"])
    assert result.exit_code != 0
    assert "cohere" in result.output.lower()
    assert "Traceback" not in result.output


def test_cli_llm_end_to_end_flags_contradiction(tmp_path: Path, monkeypatch):
    (tmp_path / "a.md").write_text("Refunds are processed within 5 days.")
    (tmp_path / "b.md").write_text("Refunds are processed within 30 days.")
    monkeypatch.setattr("corpuslint.cli.get_embedder", lambda name, cfg: _AllSameEmbedder())
    monkeypatch.setattr("corpuslint.cli.get_llm_client", lambda provider, model: _StubLLM("YES"))
    result = runner.invoke(app, [str(tmp_path), "--llm"])
    assert result.exit_code == 0  # no --fail-under given
    assert "contradict" in result.output.lower()


def test_cli_no_llm_flag_exits_zero(tmp_path: Path):
    (tmp_path / "a.md").write_text("hello world")
    result = runner.invoke(app, [str(tmp_path), "--embedder", "none"])
    assert result.exit_code == 0


def test_cli_llm_extra_flags_forwarded(tmp_path: Path, monkeypatch):
    """--llm-provider, --llm-model, --llm-max-pairs must be parsed and forwarded to get_llm_client."""
    (tmp_path / "a.md").write_text("The refund window is 5 days.")
    (tmp_path / "b.md").write_text("The refund window is 30 days.")

    captured: dict = {}

    def _fake_get_llm_client(provider: str, model: str):
        captured["provider"] = provider
        captured["model"] = model
        return _StubLLM("NO")

    monkeypatch.setattr("corpuslint.cli.get_llm_client", _fake_get_llm_client)
    monkeypatch.setattr("corpuslint.cli.get_embedder", lambda name, cfg: _AllSameEmbedder())

    result = runner.invoke(
        app,
        [
            str(tmp_path),
            "--llm",
            "--llm-provider", "azure",
            "--llm-model", "gpt-4-deployment",
            "--llm-max-pairs", "7",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "BadParameter" not in result.output
    assert captured.get("provider") == "azure"
    assert captured.get("model") == "gpt-4-deployment"


def test_cli_azure_source_end_to_end_flags_duplicates(monkeypatch):
    docs = [
        Document(text="Refunds take 5 days.", source="azure-search://kb/1"),
        Document(text="Refunds take 5 days.", source="azure-search://kb/2"),
    ]
    monkeypatch.setattr("corpuslint.sources.azure_search.load_azure_documents", lambda index, cfg: docs)
    result = runner.invoke(app, ["--source", "azure-search", "--index", "kb", "--embedder", "none"])
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "duplicate" in result.output.lower()


def test_cli_azure_requires_index(monkeypatch):
    monkeypatch.setattr("corpuslint.sources.azure_search.load_azure_documents", lambda index, cfg: [])
    result = runner.invoke(app, ["--source", "azure-search", "--embedder", "none"])
    assert result.exit_code != 0
    assert "index" in result.output.lower()
    assert "Traceback" not in result.output


def test_cli_azure_connector_error_is_clean(monkeypatch):
    def _boom(index, cfg):
        raise AzureSearchError("AZURE_SEARCH_ENDPOINT is not set.")

    monkeypatch.setattr("corpuslint.sources.azure_search.load_azure_documents", _boom)
    result = runner.invoke(app, ["--source", "azure-search", "--index", "kb", "--embedder", "none"])
    assert result.exit_code == 1
    assert "AZURE_SEARCH_ENDPOINT" in result.output
    assert "Traceback" not in result.output


def test_cli_azure_forwards_field_config(monkeypatch):
    captured: dict = {}

    def _capture(index, cfg):
        captured["index"] = index
        captured["content_field"] = cfg.content_field
        captured["id_field"] = cfg.id_field
        return [Document(text="hi", source="azure-search://kb/1")]

    monkeypatch.setattr("corpuslint.sources.azure_search.load_azure_documents", _capture)
    result = runner.invoke(
        app,
        [
            "--source", "azure-search",
            "--index", "kb",
            "--content-field", "body",
            "--id-field", "key",
            "--embedder", "none",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured == {"index": "kb", "content_field": "body", "id_field": "key"}


def test_cli_files_source_requires_paths():
    result = runner.invoke(app, ["--embedder", "none"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_cli_source_opt_parses_into_config(monkeypatch):
    """--source-opt key=value (repeatable) is parsed into cfg.source_options."""
    captured: dict = {}

    def _capture(index, cfg):
        captured["source_options"] = dict(cfg.source_options)
        captured["index"] = index
        return [Document(text="hi", source="azure-search://kb/1")]

    monkeypatch.setattr("corpuslint.sources.azure_search.load_azure_documents", _capture)
    result = runner.invoke(
        app,
        [
            "--source", "azure-search",
            "--source-opt", "index=kb",
            "--source-opt", "content_field=body",
            "--embedder", "none",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["source_options"] == {"index": "kb", "content_field": "body"}


def test_cli_azure_reads_index_from_source_opt(monkeypatch):
    """A source reads its option from cfg.source_options: azure gets its index there."""
    captured: dict = {}

    def _capture(index, cfg):
        captured["index"] = index
        return [Document(text="hi", source="azure-search://kb/1")]

    monkeypatch.setattr("corpuslint.sources.azure_search.load_azure_documents", _capture)
    result = runner.invoke(
        app, ["--source", "azure-search", "--source-opt", "index=kb", "--embedder", "none"]
    )
    assert result.exit_code == 0, result.output
    assert captured["index"] == "kb"


def test_cli_confluence_source_end_to_end_flags_duplicates(monkeypatch):
    """--source confluence with the HTTP layer stubbed runs the whole pipeline."""
    dup = "<p>Refunds take 5 days.</p>"
    monkeypatch.setenv("CONFLUENCE_EMAIL", "me@corp.example")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")

    def _fake_http(url, email, api_token):
        return {
            "results": [
                {"id": "1", "title": "Refunds", "body": {"storage": {"value": dup}}},
                {"id": "2", "title": "Refunds", "body": {"storage": {"value": dup}}},
            ],
            "size": 2,
            "limit": 50,
            "start": 0,
        }

    monkeypatch.setattr("corpuslint.sources.confluence._http_get_json", _fake_http)
    result = runner.invoke(
        app,
        [
            "--source", "confluence",
            "--source-opt", "space=NCPCS",
            "--source-opt", "base_url=https://x.atlassian.net",
            "--embedder", "none",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "duplicate" in result.output.lower()


def test_cli_confluence_requires_space():
    result = runner.invoke(
        app,
        ["--source", "confluence", "--source-opt", "base_url=https://x.atlassian.net", "--embedder", "none"],
    )
    assert result.exit_code == 1
    assert "space" in result.output.lower()
    assert "Traceback" not in result.output


def test_cli_confluence_missing_creds_is_clean(monkeypatch):
    monkeypatch.delenv("CONFLUENCE_EMAIL", raising=False)
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")
    result = runner.invoke(
        app,
        [
            "--source", "confluence",
            "--source-opt", "space=NCPCS",
            "--source-opt", "base_url=https://x.atlassian.net",
            "--embedder", "none",
        ],
    )
    assert result.exit_code == 1
    assert "CONFLUENCE_EMAIL" in result.output
    assert "Traceback" not in result.output


def test_cli_notion_source_end_to_end_flags_duplicates(monkeypatch):
    """--source notion with the HTTP layer stubbed runs the whole pipeline."""
    monkeypatch.setenv("NOTION_TOKEN", "tok")

    def _fake_http(url, token, method="GET", payload=None):
        if "/query" in url:
            return {
                "results": [
                    {"id": "1", "url": "https://notion.so/1",
                     "properties": {"Name": {"type": "title", "title": [{"plain_text": "Refunds"}]}}},
                    {"id": "2", "url": "https://notion.so/2",
                     "properties": {"Name": {"type": "title", "title": [{"plain_text": "Refunds"}]}}},
                ],
                "has_more": False,
                "next_cursor": None,
            }
        return {
            "results": [{"id": "b", "type": "paragraph", "has_children": False,
                         "paragraph": {"rich_text": [{"plain_text": "Refunds take 5 days."}]}}],
            "has_more": False,
            "next_cursor": None,
        }

    monkeypatch.setattr("corpuslint.sources.notion._http_request_json", _fake_http)
    result = runner.invoke(
        app,
        ["--source", "notion", "--source-opt", "database_id=db-1", "--embedder", "none"],
    )
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "duplicate" in result.output.lower()


def test_cli_notion_requires_database_id():
    result = runner.invoke(app, ["--source", "notion", "--embedder", "none"])
    assert result.exit_code == 1
    assert "database" in result.output.lower()
    assert "Traceback" not in result.output


def test_cli_notion_missing_token_is_clean(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    result = runner.invoke(
        app,
        ["--source", "notion", "--source-opt", "database_id=db-1", "--embedder", "none"],
    )
    assert result.exit_code == 1
    assert "NOTION_TOKEN" in result.output
    assert "Traceback" not in result.output


def test_cli_unknown_source_is_clean():
    result = runner.invoke(app, ["--source", "bogus", "--embedder", "none"])
    assert result.exit_code != 0
    assert "bogus" in result.output.lower()
    assert "files" in result.output.lower()
    assert "Traceback" not in result.output


def test_cli_json_output_and_fail_under(tmp_path: Path):
    (tmp_path / "a.md").write_text("Cats are great pets.")
    (tmp_path / "b.md").write_text("Cats are great pets.")
    result = runner.invoke(app, [str(tmp_path), "--embedder", "none", "--json", "--fail-under", "100"])
    assert result.exit_code == 1  # duplicates drop score below 100
    assert '"score"' in result.stdout


def test_cli_source_opt_no_equals_is_clean_error():
    """--source-opt without '=' must produce a descriptive error, not a traceback."""
    result = runner.invoke(app, ["--source", "azure-search", "--source-opt", "justkey", "--embedder", "none"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "key=value" in result.output.lower() or "justkey" in result.output.lower()


def test_cli_source_opt_empty_key_is_clean_error():
    """--source-opt =value (empty key) must produce a descriptive error, not a
    traceback, and must not insert a "" key into source_options."""
    result = runner.invoke(app, ["--source", "azure-search", "--source-opt", "=value", "--embedder", "none"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "empty" in result.output.lower() and "key" in result.output.lower()


def test_cli_source_opt_value_with_equals_is_accepted(monkeypatch):
    """--source-opt a=b=c must parse as key='a', value='b=c' (split on first '=' only)."""
    captured: dict = {}

    def _capture(index, cfg):
        captured["source_options"] = dict(cfg.source_options)
        return [Document(text="hi", source="azure-search://kb/1")]

    monkeypatch.setattr("corpuslint.sources.azure_search.load_azure_documents", _capture)
    result = runner.invoke(
        app,
        ["--source", "azure-search", "--index", "kb", "--source-opt", "url=https://example.com/path=1", "--embedder", "none"],
    )
    assert result.exit_code == 0, result.output
    assert captured["source_options"]["url"] == "https://example.com/path=1"


def test_cli_jsonl_fast_path_finds_duplicates(tmp_path: Path):
    """Passing a .jsonl file to the CLI (--source files) must use the pre-chunked
    fast-path — duplicates in the .jsonl must be detected, proving the file was
    read via load_prechunked_jsonl, not skipped."""
    import json as _json
    jl = tmp_path / "chunks.jsonl"
    jl.write_text(
        _json.dumps({"id": "c1", "text": "Refunds take five business days.", "source": "kb/1"}) + "\n"
        + _json.dumps({"id": "c2", "text": "Refunds take five business days.", "source": "kb/2"}) + "\n"
    )
    result = runner.invoke(app, [str(jl), "--embedder", "none"])
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "duplicate" in result.output.lower()
