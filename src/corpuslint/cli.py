from __future__ import annotations

from pathlib import Path

import typer

from .analyze import analyze
from .config import load_config
from .embedder import get_embedder
from .llm_clients import LLMClientError, get_llm_client
from .report import render_html, render_json, render_terminal
from .sources import SourceError, UnknownSourceError, get_source

app = typer.Typer(add_completion=False, help="A linter for your RAG knowledge base.")


@app.command()
def main(
    paths: list[str] = typer.Argument(
        None, help="Files or directories (or .jsonl of pre-chunked data). Optional with --source azure-search."
    ),
    config: str | None = typer.Option(None, "--config"),
    source: str | None = typer.Option(
        None, "--source", help="files | azure-search (default: files)"
    ),
    index: str | None = typer.Option(None, "--index", help="Azure AI Search index name (with --source azure-search)"),
    content_field: str | None = typer.Option(
        None, "--content-field", help="Azure field holding the document text (default: content)"
    ),
    id_field: str | None = typer.Option(
        None, "--id-field", help="Azure field holding the document id (default: id)"
    ),
    source_opt: list[str] = typer.Option(
        None,
        "--source-opt",
        help="generic source option as key=value (repeatable), e.g. --source-opt index=kb",
    ),
    embedder: str = typer.Option("local", "--embedder", help="local | none"),
    llm: bool = typer.Option(False, "--llm", help="enable the LLM contradiction check"),
    llm_provider: str = typer.Option(
        "openai",
        "--llm-provider",
        help="openai | azure. azure reads AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, "
        "AZURE_OPENAI_API_VERSION (default 2024-10-21) from the environment.",
    ),
    llm_model: str = typer.Option("", "--llm-model", help="model name (or Azure deployment); blank = provider default"),
    llm_max_pairs: int | None = typer.Option(
        None, "--llm-max-pairs", help="cap LLM contradiction calls, cost guard (0 = skip all pairs)"
    ),
    json_out: bool = typer.Option(False, "--json"),
    html: str | None = typer.Option(None, "--html", help="write an HTML report to this path"),
    fail_under: int | None = typer.Option(None, "--fail-under"),
) -> None:
    cfg = load_config(config)
    if fail_under is not None:
        cfg.fail_under = fail_under
    # CLI flags override .corpuslint.yml only when explicitly passed.
    if source is not None:
        cfg.source = source
    if index is not None:
        cfg.index = index
    if content_field is not None:
        cfg.content_field = content_field
    if id_field is not None:
        cfg.id_field = id_field
    cfg.paths = list(paths) if paths else []
    for item in source_opt or []:
        if "=" not in item:
            raise typer.BadParameter(
                f"expected key=value, got {item!r}", param_hint="--source-opt"
            )
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(
                f"empty option key in {item!r}", param_hint="--source-opt"
            )
        cfg.source_options[key] = value.strip()

    llm_client = None
    if llm:
        if llm_provider not in ("openai", "azure"):
            raise typer.BadParameter(
                f"unknown provider {llm_provider!r} (expected 'openai' or 'azure')",
                param_hint="--llm-provider",
            )
        cfg.use_llm = True
        cfg.llm_provider = llm_provider
        cfg.llm_model = llm_model
        if llm_max_pairs is not None:
            cfg.llm_max_pairs = llm_max_pairs
        try:
            llm_client = get_llm_client(cfg.llm_provider, cfg.llm_model)
        except LLMClientError as e:
            # Missing extra / env var is a configuration problem, not a bad CLI argument.
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from e

    emb = get_embedder(embedder, cfg)

    try:
        src = get_source(cfg.source)
    except UnknownSourceError as e:
        raise typer.BadParameter(str(e), param_hint="--source") from e

    if cfg.source == "files":
        # Keep the files fast-path on the paths route so pre-chunked .jsonl inputs
        # flow straight through the pipeline (FilesSource loads documents only).
        if not cfg.paths:
            raise typer.BadParameter("provide files or directories to check (or use --source azure-search)")
        report = analyze(cfg.paths, cfg, embedder=emb, llm=llm_client)
    else:
        try:
            documents = src.load(cfg)
        except SourceError as e:
            # Missing option / extra / env var / SDK failure is a config problem, not a bad CLI argument.
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from e
        report = analyze([], cfg, embedder=emb, llm=llm_client, documents=documents)

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
