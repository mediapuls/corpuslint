import corpuslint


def test_version_is_exposed():
    assert isinstance(corpuslint.__version__, str)
