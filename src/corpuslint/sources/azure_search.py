from __future__ import annotations

import os
import sys
import warnings

from ..config import Config
from ..models import Document
from .base import SourceError, register

# Tried in order when the configured id field is absent on a document, so a
# document still gets a stable-ish source instead of being dropped.
_ID_FALLBACKS = ("id", "key", "@search.documentKey")


class AzureSearchError(SourceError):
    """Raised when the Azure AI Search source cannot run (missing extra, missing env)."""


def _import_sdk():
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
    except ImportError as e:
        raise AzureSearchError(
            'The Azure AI Search source needs the optional extra: pip install "corpuslint[azure]"'
        ) from e
    return SearchClient, AzureKeyCredential


def _read_credentials() -> tuple[str, str]:
    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    api_key = os.environ.get("AZURE_SEARCH_API_KEY")
    if not endpoint:
        raise AzureSearchError(
            "AZURE_SEARCH_ENDPOINT is not set. Export it to use --source azure-search."
        )
    if not api_key:
        raise AzureSearchError(
            "AZURE_SEARCH_API_KEY is not set. Export it to use --source azure-search."
        )
    return endpoint, api_key


def _select_fields(config: Config) -> list[str]:
    """Ordered-unique list of fields to fetch: content, id, and the id fallbacks.

    Restricting the query to these keeps embedding vectors (often the bulk of a
    document) out of the response. ``@``-prefixed names are system annotations —
    not selectable fields, returned regardless — so they are excluded here while
    still being honoured by the read-time fallback chain.
    """
    seen: set[str] = set()
    fields: list[str] = []
    for name in (config.content_field, config.id_field, *_ID_FALLBACKS):
        if not name or name.startswith("@") or name in seen:
            continue
        seen.add(name)
        fields.append(name)
    return fields


def _doc_id(item, id_field: str, fallback: int) -> str:
    if id_field and item.get(id_field) is not None:
        return str(item.get(id_field))
    for key in _ID_FALLBACKS:
        if item.get(key) is not None:
            return str(item.get(key))
    return str(fallback)


def _documents_from_client(client, index: str, config: Config) -> list[Document]:
    """Page through every document in the index and map each to a Document.

    Iterating ``.by_page()`` follows the SDK's continuation tokens, so this
    pulls the whole index rather than a single capped page.
    """
    content_field = config.content_field
    docs: list[Document] = []
    position = 0
    for page in client.search(search_text="*", select=_select_fields(config)).by_page():
        for item in page:
            content = item.get(content_field)
            if content is None:
                warnings.warn(
                    f"skipping an Azure document missing the content field {content_field!r}",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            doc_id = _doc_id(item, config.id_field, position)
            docs.append(Document(text=str(content), source=f"azure-search://{index}/{doc_id}"))
            position += 1
    return docs


def load_azure_documents(index: str, config: Config) -> list[Document]:
    SearchClient, AzureKeyCredential = _import_sdk()
    endpoint, api_key = _read_credentials()
    client = SearchClient(
        endpoint=endpoint,
        index_name=index,
        credential=AzureKeyCredential(api_key),
    )
    docs = _documents_from_client(client, index, config)
    # Report the count on stderr (keeps stdout/--json clean) to back up the
    # "pages through everything, no silent cap" guarantee.
    print(f"fetched {len(docs)} documents from index {index!r}", file=sys.stderr)
    return docs


class AzureSearchSource:
    name = "azure-search"

    def load(self, config: Config) -> list[Document]:
        # Options come from the generic source_options bag first, falling back to
        # the dedicated legacy fields (--index / --content-field / --id-field).
        opts = config.source_options
        index = opts.get("index") or config.index
        if not index:
            raise SourceError(
                "the azure-search source requires an index "
                "(pass --index, --source-opt index=..., or set it in .corpuslint.yml)"
            )
        # Field overrides via source_options need no dedicated flag; they feed the
        # same config load_azure_documents already reads.
        if "content_field" in opts:
            config.content_field = opts["content_field"]
        if "id_field" in opts:
            config.id_field = opts["id_field"]
        return load_azure_documents(index, config)


register(AzureSearchSource())
