from pathlib import Path

from corpuslint.config import load_config


def test_defaults_when_no_file():
    cfg = load_config(None)
    assert cfg.near_dupe_threshold == 0.95
    assert "exact_duplicates" in cfg.enabled_checks


def test_yaml_overrides_only_present_keys(tmp_path: Path):
    p = tmp_path / ".corpuslint.yml"
    p.write_text("near_dupe_threshold: 0.8\nfail_under: 70\n")
    cfg = load_config(str(p))
    assert cfg.near_dupe_threshold == 0.8
    assert cfg.fail_under == 70
    assert cfg.min_chunk_tokens == 20  # untouched default


def test_llm_defaults():
    cfg = load_config(None)
    assert cfg.llm_provider == "openai"
    assert cfg.llm_model == ""
    assert cfg.llm_max_pairs == 200


def test_llm_yaml_overrides(tmp_path: Path):
    p = tmp_path / ".corpuslint.yml"
    p.write_text("llm_provider: azure\nllm_model: my-deployment\nllm_max_pairs: 50\n")
    cfg = load_config(str(p))
    assert cfg.llm_provider == "azure"
    assert cfg.llm_model == "my-deployment"
    assert cfg.llm_max_pairs == 50


def test_source_defaults():
    cfg = load_config(None)
    assert cfg.source == "files"
    assert cfg.index == ""
    assert cfg.content_field == "content"
    assert cfg.id_field == "id"


def test_source_yaml_overrides(tmp_path: Path):
    p = tmp_path / ".corpuslint.yml"
    p.write_text("source: azure-search\nindex: kb\ncontent_field: body\nid_field: key\n")
    cfg = load_config(str(p))
    assert cfg.source == "azure-search"
    assert cfg.index == "kb"
    assert cfg.content_field == "body"
    assert cfg.id_field == "key"


def test_source_options_default_empty():
    cfg = load_config(None)
    assert cfg.source_options == {}
    assert cfg.paths == []


def test_source_options_yaml_override(tmp_path: Path):
    p = tmp_path / ".corpuslint.yml"
    p.write_text("source: azure-search\nsource_options:\n  index: kb\n  content_field: body\n")
    cfg = load_config(str(p))
    assert cfg.source_options == {"index": "kb", "content_field": "body"}
