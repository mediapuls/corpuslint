# corpuslint

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

## Checks
exact duplicates · near duplicates · low-information chunks · chunk-size
anomalies · embedding outliers · contradictions (opt-in).

## Config
Optional `.corpuslint.yml` overrides thresholds and check selection.

## Architecture
Library-first: `corpuslint.analyze(paths, config) -> Report`. The CLI is a thin
wrapper; an MCP server, Azure AI Search connector, eval-set generation, and
drift monitoring are on the roadmap.

MIT licensed.
