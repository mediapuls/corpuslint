from __future__ import annotations

import os
import warnings

from ..config import Config
from ..models import Document

# Tried in order when the configured id field is absent on a document, so a
# document still gets a stable-ish source instead of being dropped.
_ID_FALLBACKS = ("id", "key", "@search.documentKey")


class AzureSearchError(RuntimeError):
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
    for page in client.search(search_text="*").by_page():
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
    return _documents_from_client(client, index, config)
