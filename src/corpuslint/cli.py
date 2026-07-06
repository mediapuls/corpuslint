from __future__ import annotations

from pathlib import Path

import typer

from .analyze import analyze
from .config import load_config
from .embedder import get_embedder
from .report import render_html, render_json, render_terminal

app = typer.Typer(add_completion=False, help="A linter for your RAG knowledge base.")


@app.command()
def main(
    paths: list[str] = typer.Argument(..., help="Files or directories (or .jsonl of pre-chunked data)."),
    config: str | None = typer.Option(None, "--config"),
    embedder: str = typer.Option("local", "--embedder", help="local | none"),
    llm: bool = typer.Option(False, "--llm", help="enable the LLM contradiction check"),
    json_out: bool = typer.Option(False, "--json"),
    html: str | None = typer.Option(None, "--html", help="write an HTML report to this path"),
    fail_under: int | None = typer.Option(None, "--fail-under"),
) -> None:
    if llm:
        raise typer.BadParameter(
            "--llm needs an LLM backend, which the CLI does not yet provide. "
            "Use the library API instead: analyze(paths, config, llm=your_client). "
            "See the roadmap."
        )
    cfg = load_config(config)
    if fail_under is not None:
        cfg.fail_under = fail_under
    emb = get_embedder(embedder, cfg)
    report = analyze(paths, cfg, embedder=emb, llm=None)

    if json_out:
        typer.echo(render_json(report))
    else:
        render_terminal(report)
    if html:
        Path(html).write_text(render_html(report), encoding="utf-8")

    if cfg.fail_under is not None and report.score < cfg.fail_under:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
