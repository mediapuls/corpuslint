# corpuslint

[![PyPI version](https://img.shields.io/pypi/v/corpuslint.svg)](https://pypi.org/project/corpuslint/)
[![Python versions](https://img.shields.io/pypi/pyversions/corpuslint.svg)](https://pypi.org/project/corpuslint/)
[![License: MIT](https://img.shields.io/pypi/l/corpuslint.svg)](https://github.com/mediapuls/corpuslint/blob/main/LICENSE)

**A linter for your RAG knowledge base.** RAGAS & co. evaluate the *answer*.
`corpuslint` evaluates the *data* that feeds it — before it reaches your users.

## Why
Most bad RAG answers are a corpus problem, not a model problem: duplicates,
near-duplicates, low-information chunks, size anomalies, embedding outliers,
and contradictions. `corpuslint` scores your corpus and shows you exactly what
to fix.

## Install
```bash
pip install corpuslint            # core, runs offline and free
pip install "corpuslint[local]"   # + local embeddings (near-dupes, outliers)
pip install "corpuslint[llm]"     # + LLM contradiction check (OpenAI / Azure OpenAI)
pip install "corpuslint[azure]"   # + Azure AI Search source connector
pip install "corpuslint[mcp]"     # + MCP server (lint a corpus from an AI agent)
```

## Use
```bash
corpuslint ./docs                       # terminal report
corpuslint ./docs --html report.html    # shareable HTML
corpuslint ./docs --fail-under 70       # CI gate (exit 1 if score < 70)
corpuslint ./chunks.jsonl               # pre-chunked input

# LLM contradiction check (needs the [llm] extra + an API key):
export OPENAI_API_KEY=sk-...
corpuslint ./docs --llm                             # OpenAI, default gpt-4o-mini
corpuslint ./docs --llm --llm-model gpt-4o          # pick a model
corpuslint ./docs --llm --llm-max-pairs 50          # cap paid calls (default 200)

# Azure OpenAI — reads AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT and
# AZURE_OPENAI_API_VERSION (default 2024-10-21) from the environment.
# --llm-model is the Azure *deployment* name.
export AZURE_OPENAI_API_KEY=...  AZURE_OPENAI_ENDPOINT=https://<res>.openai.azure.com
corpuslint ./docs --llm --llm-provider azure --llm-model my-deployment
```

The contradiction check is O(n²): it prefilters candidate pairs by embedding
similarity, then asks the LLM about each. `--llm-max-pairs` bounds how many pairs
reach the LLM (highest-similarity first) so cost stays predictable; skipped pairs
are reported (`--llm-max-pairs 0` skips the LLM entirely).

## Sources

By default corpuslint reads files and directories. It can also pull the corpus
straight from a vector store and run the same checks on it.

### Azure AI Search
Needs the `[azure]` extra. Reads the endpoint and admin/query key from the
environment; the index is passed with `--index`:

```bash
pip install "corpuslint[azure]"
export AZURE_SEARCH_ENDPOINT=https://<service>.search.windows.net
export AZURE_SEARCH_API_KEY=<key>

corpuslint --source azure-search --index my-index
corpuslint --source azure-search --index my-index --content-field body --id-field key
```

It pages through **every** document in the index (no silent cap), maps each to a
document whose source is `azure-search://<index>/<id>`, and feeds them through the
normal chunking + check pipeline. `--content-field` (default `content`) selects the
field holding the text; `--id-field` (default `id`) selects the id field. Documents
missing the content field are skipped with a warning.

### Source options

Every source reads its settings from a generic bag, so no source needs its own
global flag. Pass options with the repeatable `--source-opt key=value`, or set a
`source_options:` block in `.corpuslint.yml`:

```bash
corpuslint --source azure-search --source-opt index=my-index --source-opt content_field=body
```

```yaml
source: azure-search
source_options:
  index: my-index
  content_field: body
```

For Azure AI Search the dedicated flags (`--index`, `--content-field`,
`--id-field`) still work and are equivalent to the matching `--source-opt`.

### Adding a source

A source is one small module plus a registry entry — no `cli.py` changes:

1. Implement the `Source` protocol (`corpuslint.sources.base.Source`): a `name`
   attribute and `load(config) -> list[Document]`. Read your settings from
   `config.source_options` (the `--source-opt` / YAML bag). Raise
   `SourceError` with a clear message when a required option is missing or the
   backend fails — the CLI turns it into a clean error, never a traceback.
2. Register the instance with `register(...)`:

   ```python
   # corpuslint/sources/notion.py
   from ..config import Config
   from ..models import Document
   from .base import SourceError, register


   class NotionSource:
       name = "notion"

       def load(self, config: Config) -> list[Document]:
           token = config.source_options.get("token")
           if not token:
               raise SourceError("the notion source requires --source-opt token=...")
           ...  # fetch pages, return Documents
           return docs


   register(NotionSource())
   ```

3. Import the module in `corpuslint/sources/__init__.py` so it registers on load.

Now `corpuslint --source notion --source-opt token=...` works end to end.

## MCP server
Needs the `[mcp]` extra. `corpuslint-mcp` runs a stdio [Model Context
Protocol](https://modelcontextprotocol.io) server so an AI agent (Claude Desktop,
etc.) can lint a corpus and get back the Quality Score plus findings.

```bash
pip install "corpuslint[mcp]"
corpuslint-mcp        # stdio server; usually launched by the MCP client, not by hand
```

It exposes one tool, `lint_corpus(path, embedder="local", fail_under=None)`, which
returns a structured dict: `score`, `total_chunks`, `counts_by_check`,
`top_offenders`, and `findings`. With `embedder="local"` but the `[local]` extra
missing, it falls back to `embedder="none"` (semantic checks skipped) and adds a
`warning` instead of failing.

Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "corpuslint": {
      "command": "corpuslint-mcp"
    }
  }
}
```

## Checks
exact duplicates · near duplicates · low-information chunks · chunk-size
anomalies · embedding outliers · contradictions (opt-in).

## Config
Optional `.corpuslint.yml` overrides thresholds and check selection.

## Architecture
Library-first: `corpuslint.analyze(paths, config) -> Report`. The CLI is a thin
wrapper; the same API backs the CLI, the MCP server, and the source connectors.
Sources are pluggable through a small registry (`corpuslint.sources`) — see
[Adding a source](#adding-a-source). Eval-set generation and drift monitoring are
on the roadmap.

MIT licensed.
