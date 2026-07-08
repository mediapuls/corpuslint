from .azure_search import AzureSearchSource  # noqa: F401  (registers azure-search)
from .base import REGISTRY, Source, SourceError, UnknownSourceError, get_source, register
from .confluence import ConfluenceSource  # noqa: F401  (registers confluence)
from .files import FilesSource  # noqa: F401  (registers files)
from .notion import NotionSource  # noqa: F401  (registers notion)

__all__ = [
    "REGISTRY",
    "Source",
    "SourceError",
    "UnknownSourceError",
    "get_source",
    "register",
]
