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
```

## Use
```bash
corpuslint ./docs                       # terminal report
corpuslint ./docs --html report.html    # shareable HTML
corpuslint ./docs --fail-under 70       # CI gate (exit 1 if score < 70)
corpuslint ./chunks.jsonl               # pre-chunked input
                                        # LLM contradiction check: library API only
                                        #   analyze(paths, config, llm=your_client)
                                        #   CLI backend on the roadmap
```

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
