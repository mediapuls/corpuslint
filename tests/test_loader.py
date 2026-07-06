from pathlib import Path

from corpuslint.config import Config
from corpuslint.tokenize import count_tokens
from corpuslint.loader import load_documents


def test_count_tokens():
    assert count_tokens("hello world  foo") == 3


def test_loads_txt_md_and_strips_html(tmp_path: Path):
    (tmp_path / "a.md").write_text("# Title\nbody")
    (tmp_path / "b.html").write_text("<p>Hello <b>world</b></p>")
    (tmp_path / "skip.png").write_text("nope")
    docs = load_documents([str(tmp_path)], Config())
    by_source = {Path(d.source).name: d.text for d in docs}
    assert "skip.png" not in by_source
    assert "Hello world" in by_source["b.html"]
    assert "body" in by_source["a.md"]
