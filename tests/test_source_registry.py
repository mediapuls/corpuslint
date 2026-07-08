import pytest

from corpuslint.sources.base import UnknownSourceError, get_source


def test_get_source_returns_files_source():
    src = get_source("files")
    assert src.name == "files"


def test_get_source_returns_azure_search_source():
    src = get_source("azure-search")
    assert src.name == "azure-search"


def test_get_source_returns_notion_source():
    src = get_source("notion")
    assert src.name == "notion"


def test_get_source_unknown_raises_listing_valid_names():
    with pytest.raises(UnknownSourceError) as exc:
        get_source("bogus")
    msg = str(exc.value)
    assert "bogus" in msg
    # the error must list the valid sources so the user can recover
    assert "files" in msg
    assert "azure-search" in msg
