from pathlib import Path
from typer.testing import CliRunner
from corpuslint.cli import app
from corpuslint.llm_clients import LLMClientError

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
    assert result.exit_code != 0
    assert "OPENAI_API_KEY" in result.output
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


def test_cli_json_output_and_fail_under(tmp_path: Path):
    (tmp_path / "a.md").write_text("Cats are great pets.")
    (tmp_path / "b.md").write_text("Cats are great pets.")
    result = runner.invoke(app, [str(tmp_path), "--embedder", "none", "--json", "--fail-under", "100"])
    assert result.exit_code == 1  # duplicates drop score below 100
    assert '"score"' in result.stdout
