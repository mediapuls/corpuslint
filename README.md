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

### Confluence
No extra needed — the connector uses only the standard library. Reads the
account email and API token from the environment; the space key is passed with
`--source-opt space=<KEY>`. The base URL can come from `--source-opt base_url=...`
or the `CONFLUENCE_BASE_URL` env var:

```bash
export CONFLUENCE_BASE_URL=https://<site>.atlassian.net
export CONFLUENCE_EMAIL=you@example.com
export CONFLUENCE_API_TOKEN=<token>   # id.atlassian.com/manage-profile/security/api-tokens

corpuslint --source confluence --source-opt space=NCPCS
# base URL can also be passed inline instead of via env:
corpuslint --source confluence --source-opt space=NCPCS --source-opt base_url=https://acme.atlassian.net
```

It pages through **every** current page in the space (no silent cap), prepends
the page title as an `<h1>` to the storage-format body, strips the XHTML/`<ac:…>`
macros to clean text (the same extractor used for local `.html` files), and maps
each to a document whose source is
`https://<site>.atlassian.net/wiki/spaces/<KEY>/pages/<id>`. Pages with an empty
body are skipped with a warning. Credentials are read from the environment only —
never from `--source-opt` or `.corpuslint.yml` — so tokens don't land in config.

### Notion
No extra needed — the connector uses only the standard library. Reads the
integration token from the environment; the database is passed with
`--source-opt database_id=<id>`:

```bash
export NOTION_TOKEN=<secret_token>   # notion.so/my-integrations → Internal Integration Secret

corpuslint --source notion --source-opt database_id=1a2b3c4d5e6f7890abcdef1234567890
```

Create an internal integration, then **share the database with it** (database
`•••` menu → Connections) so the token can see it. The connector queries the
database (`POST /v1/databases/{id}/query`, paged via `start_cursor` — **every**
row, no silent cap), then for each page pulls its blocks
(`GET /v1/blocks/{id}/children`, also fully paged) and turns the supported block
types (paragraph, headings, list items, to-dos, quotes, callouts, code) into
text, recursing into nested blocks up to a bounded depth. Each page maps to a
document whose text is the page title plus its block text and whose source is the
page URL (falling back to `notion://<page_id>`). Pages with no title and no text
are skipped with a warning. The token is read from the environment only — never
from `--source-opt` or `.corpuslint.yml` — so it doesn't land in config.

### Web (docs site)
No extra needed — the connector uses only the standard library. It pulls a docs
website in one of two modes, passed via `--source-opt`:

```bash
# sitemap mode (recommended): fetch every URL in a sitemap
corpuslint --source web --source-opt sitemap=https://docs.acme.com/sitemap.xml

# crawl mode: BFS from a start URL, following same-domain links
corpuslint --source web --source-opt url=https://docs.acme.com/ --source-opt depth=2 --source-opt max_pages=200
```

**Sitemap mode** (`sitemap=<url>`) fetches the sitemap, follows nested
sitemap-index files, and pulls every listed page. **Crawl mode** (`url=<start>`)
does a breadth-first crawl from the start URL, extracting `<a href>` links from
each page and following the same-domain ones. Either way each page's HTML is run
through the same extractor used for local `.html` files and mapped to a document
whose source is the page URL.

The crawler is bounded and polite by default:

| `--source-opt` | Default | Meaning |
|---|---|---|
| `depth` | `2` | crawl-mode link depth from the start URL |
| `max_pages` | `200` | hard cap on pages fetched — never unbounded; a warning is emitted when the cap truncates the run |
| `delay` | `0.5` | seconds between requests, so we don't hammer the site. A robots.txt `Crawl-delay` raises this (the larger of the two wins) |

Additional guardrails, always on:
- **robots.txt is respected** — disallowed URLs are skipped, and a `Crawl-delay`
  directive is honored (per-host, cached). The delay applies to every fetch
  attempt (including 404s and non-HTML) and between sitemap sub-files.
- **Same-domain only** in crawl mode — external links are never followed.
- **Deduped** visited URLs, **bounded depth**, URL fragments stripped.
- **Non-HTML responses** (PDF, images, JSON) are skipped with a warning.
- A per-page fetch error (404/timeout) skips that page and the run continues.
- Requests send a `corpuslint/<version>` **User-Agent**.
- Sitemaps carrying a `DOCTYPE`/entity declaration are refused (XXE / billion-laughs guard).

**Not guarded — SSRF:** the crawler fetches whatever URLs you point it at and
does **not** block internal, loopback, or private-range targets (e.g.
`http://localhost/`, `http://169.254.169.254/`, `http://10.0.0.0/8` hosts). Only
run it against docs sites you trust; don't feed it attacker-controlled start
URLs or sitemaps. This is an accepted trade-off for a user-run CLI.

### S3 (object storage)
Needs the `[s3]` extra. Reads documents straight out of an S3 bucket (or any
S3-compatible store — Cloudflare R2, MinIO, Wasabi, Backblaze B2). The bucket is
passed with `--source-opt bucket=<name>`:

```bash
pip install "corpuslint[s3]"
# Credentials come from boto3's standard chain — env vars or ~/.aws/credentials:
export AWS_ACCESS_KEY_ID=<key>
export AWS_SECRET_ACCESS_KEY=<secret>   # (AWS_SESSION_TOKEN too, if you use one)

corpuslint --source s3 --source-opt bucket=my-docs-bucket
# only objects under a prefix:
corpuslint --source s3 --source-opt bucket=my-docs-bucket --source-opt prefix=docs/

# S3-compatible store (Cloudflare R2 shown); endpoint_url switches provider:
corpuslint --source s3 --source-opt bucket=my-bucket \
  --source-opt endpoint_url=https://<accountid>.r2.cloudflarestorage.com \
  --source-opt region=auto
```

| `--source-opt` | Default | Meaning |
|---|---|---|
| `bucket` | — (required) | bucket to read from |
| `prefix` | (none) | only enumerate objects whose key starts with this |
| `endpoint_url` | (none) | point at an S3-compatible store instead of AWS S3 |
| `region` | (none) | bucket region (passed to boto3 as `region_name`) |

It lists **every** object under the prefix (paginated — no silent cap), and for
each object with a supported extension (`.md`, `.txt`, `.html`, `.htm`) downloads
its bytes and runs them through the same parsers used for local files, mapping
each to a document whose source is `s3://<bucket>/<key>`. Objects with any other
extension (images, PDFs, archives, other binaries) are skipped without a
download. A per-object download or parse error skips that object with a warning
and the run continues.

**Credentials** are resolved entirely by boto3's standard chain — environment
variables, `~/.aws/credentials`, or an instance/role profile — and are **never**
read from `--source-opt` or `.corpuslint.yml`, so no secret lands in config. If
boto3 can't resolve credentials the connector fails with a clean, secret-free
error.

### SharePoint / OneDrive
Reads documents out of a SharePoint site's document library (or any drive
reachable through it) via Microsoft Graph. Stdlib-only — **no extra to install**.

**App registration (one-time).** Create an app registration in Entra ID (Azure
AD), add the **Application** permission `Sites.Read.All` under Microsoft Graph,
and have an admin **grant admin consent**. Create a client secret. This is
app-only (daemon) access — no signed-in user.

```bash
# Credentials come from the environment (standard Microsoft variable names):
export AZURE_TENANT_ID=<directory (tenant) id>
export AZURE_CLIENT_ID=<application (client) id>
export AZURE_CLIENT_SECRET=<client secret value>

# Point at a site by hostname + server-relative path:
corpuslint --source sharepoint \
  --source-opt site=contoso.sharepoint.com:/sites/Engineering

# Scope to a subfolder of the default library:
corpuslint --source sharepoint \
  --source-opt site=contoso.sharepoint.com:/sites/Engineering \
  --source-opt folder=Policies/HR

# Or address a site / drive by id directly:
corpuslint --source sharepoint --source-opt site_id=<id> --source-opt drive_id=<id>
```

| `--source-opt` | Default | Meaning |
|---|---|---|
| `site` | — (required¹) | site as `<hostname>:/sites/<path>` |
| `site_id` | — (required¹) | resolved Graph site id (skips site lookup) |
| `drive_id` | site's default library | address a specific document library/drive |
| `folder` | drive root | only walk this folder (server-relative path) |

¹ one of `site` or `site_id` is required.

It authenticates with the OAuth2 **client-credentials** flow (app-only token),
resolves the site, then walks the drive from the root (or `folder`): it recurses
into **every** folder (bounded depth) and follows Graph's `@odata.nextLink`
paging on each listing, so nothing is silently capped. For each file with a
supported extension (`.md`, `.txt`, `.html`, `.htm`) it downloads the bytes and
runs them through the same parsers used for local files, mapping each to a
document whose source is the file's `webUrl`. Files with any other extension
(images, PDFs, Office binaries, archives) are skipped without a download. A
per-file download or parse error skips that file with a warning and the run
continues.

**Credentials** are read only from the three environment variables above —
**never** from `--source-opt` or `.corpuslint.yml`, so no secret lands in config.
A missing variable, a rejected token request, or a denied Graph call fails with a
clean, secret-free error.

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
   # corpuslint/sources/example.py
   from ..config import Config
   from ..models import Document
   from .base import SourceError, register


   class ExampleSource:
       name = "example"

       def load(self, config: Config) -> list[Document]:
           dataset = config.source_options.get("dataset")
           if not dataset:
               raise SourceError("the example source requires --source-opt dataset=...")
           ...  # fetch pages, return Documents (read secrets from os.environ, not options)
           return docs


   register(ExampleSource())
   ```

3. Import the module in `corpuslint/sources/__init__.py` so it registers on load.

Now `corpuslint --source example --source-opt dataset=...` works end to end.
See `corpuslint/sources/confluence.py` and `corpuslint/sources/notion.py` for
real, stdlib-only connectors that follow this shape.

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
