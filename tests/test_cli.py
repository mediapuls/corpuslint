from pathlib import Path
from typer.testing import CliRunner
from corpuslint.cli import app

runner = CliRunner()


def test_cli_llm_flag_exits_nonzero(tmp_path: Path):
    (tmp_path / "a.md").write_text("hello world")
    result = runner.invoke(app, [str(tmp_path), "--llm"])
    assert result.exit_code != 0
    assert "LLM" in result.output or "llm" in result.output.lower()


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
