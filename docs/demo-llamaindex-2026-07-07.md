# Demo: linting the LlamaIndex docs corpus

_Run 2026-07-07 with corpuslint 0.1.0 (`[local]` embeddings, offline, no LLM)._

We pointed corpuslint at the LlamaIndex documentation (`run-llama/llama_index`,
`docs/src/content/docs/framework/`) as a real-world dogfood of a corpus a lot of
RAG builders actually index.

## Command

```bash
pip install "corpuslint[local]"
corpuslint ./llama_index/docs --html report.html --json > report.json
```

Runtime: **~44s** for 553 chunks on a laptop (local MiniLM embeddings, no API calls).

## Result: Quality Score 93 / 100

| check | count |
|---|---|
| exact_duplicates | 1 |
| near_duplicates | 37 |
| low_information | 10 |
| chunk_size | 12 |
| embedding_outliers | 7 |
| **total findings** | **67** |

553 chunks scanned.

## The standout: one file, 35 of the findings

`framework/CHANGELOG.md` alone produced **35** findings — overwhelmingly
near-duplicates. Changelog entries are structurally near-identical
("Fixed X in package Y"), so at cosine 0.95 they crowd each other out of
retrieval. A changelog rarely answers a user's question but happily pollutes
the top-k. Classic corpus problem an eval framework won't tell you about,
because it's not about the answer — it's about the data feeding it.

## Concrete examples (each real, from the report)

- **Exact duplicate across two files:** the fine-tuning intro chunk in
  `framework/optimizing/fine-tuning/fine-tuning.md` is byte-identical to the one
  in `framework/use_cases/fine_tuning.md` — two files, one chunk, wasted
  retrieval slot.
- **Near-duplicate (cosine 0.95):** consecutive `CHANGELOG.md` entries.
- **Low-information chunk:** `framework/community/faq/query_engines.md` opens
  with a 5-token chunk — too short/granular to retrieve usefully.
- **Undersized chunk (5 tokens):** same FAQ file — retrieval too granular.
- **Embedding outlier (z≈3.0):** `framework/community/integrations/uptrain.md`
  — a chunk that sits far from everything else (possible boilerplate/junk or a
  formatting artifact).

## Takeaway

LlamaIndex's docs are *good* (93/100) — this isn't a gotcha. The point is that
even a well-maintained, marquee corpus carries ~12% chunk-level noise that
silently degrades retrieval, and corpuslint surfaces exactly which chunks and
why in 44 seconds, offline and free. That's the pitch: **RAGAS & co. score the
answer; corpuslint scores the data before it gets there.**
