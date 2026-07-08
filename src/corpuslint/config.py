from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

ALL_CHECKS = [
    "exact_duplicates",
    "near_duplicates",
    "low_information",
    "chunk_size",
    "embedding_outliers",
    "contradictions",
]

DEFAULT_WEIGHTS = {
    "exact_duplicates": 1.0,
    "near_duplicates": 0.6,
    "low_information": 0.4,
    "chunk_size": 0.3,
    "embedding_outliers": 0.5,
    "contradictions": 1.5,
}


@dataclass
class Config:
    near_dupe_threshold: float = 0.95
    min_chunk_tokens: int = 20
    max_chunk_tokens: int = 1000
    low_info_min_tokens: int = 10
    outlier_zscore: float = 3.0
    target_chunk_tokens: int = 300
    use_llm: bool = False
    llm_provider: str = "openai"
    llm_model: str = ""
    llm_max_pairs: int = 200
    source: str = "files"
    index: str = ""
    content_field: str = "content"
    id_field: str = "id"
    # Input paths for the "files" source; set by the CLI from its positional args.
    paths: list[str] = field(default_factory=list)
    # Generic per-source options (e.g. from --source-opt or a source_options: block).
    # A source reads its own settings from here, so new connectors need no new flags.
    source_options: dict[str, Any] = field(default_factory=dict)
    fail_under: int | None = None
    enabled_checks: list[str] = field(default_factory=lambda: list(ALL_CHECKS))
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


def load_config(path: str | None) -> Config:
    cfg = Config()
    target = Path(path) if path else Path(".corpuslint.yml")
    if not target.exists():
        return cfg
    data = yaml.safe_load(target.read_text()) or {}
    valid = {f.name for f in fields(Config)}
    for key, value in data.items():
        if key in valid:
            setattr(cfg, key, value)
    return cfg
