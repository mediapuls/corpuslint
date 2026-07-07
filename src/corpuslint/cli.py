from __future__ import annotations

from pathlib import Path

import typer

from .analyze import analyze
from .config import load_config
from .embedder import get_embedder
from .llm_clients import LLMClientError, get_llm_client
from .report import render_html, render_json, render_terminal

app = typer.Typer(add_completion=False, help="A linter for your RAG knowledge base.")


@app.command()
def main(
    paths: list[str] = typer.Argument(..., help="Files or directories (or .jsonl of pre-chunked data)."),
    config: str | None = typer.Option(None, "--config"),
    embedder: str = typer.Option("local", "--embedder", help="local | none"),
    llm: bool = typer.Option(False, "--llm", help="enable the LLM contradiction check"),
    llm_provider: str = typer.Option("openai", "--llm-provider", help="openai | azure"),
    llm_model: str = typer.Option("", "--llm-model", help="model name (or Azure deployment); blank = provider default"),
    llm_max_pairs: int | None = typer.Option(None, "--llm-max-pairs", help="cap LLM contradiction calls (cost guard)"),
    json_out: bool = typer.Option(False, "--json"),
    html: str | None = typer.Option(None, "--html", help="write an HTML report to this path"),
    fail_under: int | None = typer.Option(None, "--fail-under"),
) -> None:
    cfg = load_config(config)
    if fail_under is not None:
        cfg.fail_under = fail_under

    llm_client = None
    if llm:
        cfg.use_llm = True
        cfg.llm_provider = llm_provider
        cfg.llm_model = llm_model
        if llm_max_pairs is not None:
            cfg.llm_max_pairs = llm_max_pairs
        try:
            llm_client = get_llm_client(cfg.llm_provider, cfg.llm_model)
        except LLMClientError as e:
            raise typer.BadParameter(str(e)) from e

    emb = get_embedder(embedder, cfg)
    report = analyze(paths, cfg, embedder=emb, llm=llm_client)

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
