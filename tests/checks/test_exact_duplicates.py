from corpuslint.config import Config
from corpuslint.models import Chunk
from corpuslint.checks.base import CheckContext, get_enabled_checks
from corpuslint.checks.exact_duplicates import ExactDuplicatesCheck


def _ctx(texts):
    chunks = [Chunk(id=str(i), text=t, source="s") for i, t in enumerate(texts)]
    return CheckContext(chunks=chunks, embeddings=None, config=Config())


def test_flags_exact_duplicate_chunks():
    findings = ExactDuplicatesCheck().run(_ctx(["a", "a", "b"]))
    assert len(findings) == 1
    assert set(findings[0].chunk_ids) == {"0", "1"}


def test_registry_respects_enabled_checks():
    cfg = Config(enabled_checks=["exact_duplicates"])
    names = [c.name for c in get_enabled_checks(cfg)]
    assert names == ["exact_duplicates"]
