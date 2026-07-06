from corpuslint.config import Config
from corpuslint.models import Chunk
from corpuslint.checks.base import CheckContext
from corpuslint.checks.contradictions import ContradictionsCheck


class StubLLM:
    def __init__(self, answer: str):
        self.answer = answer

    def complete(self, prompt: str) -> str:
        return self.answer


def _ctx(use_llm, llm):
    chunks = [Chunk("0", "price is 3.99", "s"), Chunk("1", "price is 4.99", "s")]
    embeddings = [[1.0, 0.0], [0.99, 0.1]]
    return CheckContext(chunks, embeddings, Config(use_llm=use_llm), llm=llm)


def test_off_by_default():
    assert ContradictionsCheck().run(_ctx(False, StubLLM("YES"))) == []


def test_flags_when_llm_says_yes():
    findings = ContradictionsCheck().run(_ctx(True, StubLLM("YES")))
    assert len(findings) == 1
    assert set(findings[0].chunk_ids) == {"0", "1"}


def test_no_flag_when_llm_says_no():
    assert ContradictionsCheck().run(_ctx(True, StubLLM("NO"))) == []


def test_chunk_text_with_braces_does_not_crash():
    """Regression: chunk text containing { } (code, JSON, templates) should not crash format()."""
    chunks = [
        Chunk("0", "config uses {timeout: 30}", "s"),
        Chunk("1", "config uses {timeout: 60}", "s"),
    ]
    embeddings = [[1.0, 0.0], [0.99, 0.1]]
    ctx = CheckContext(chunks, embeddings, Config(use_llm=True), llm=StubLLM("NO"))
    findings = ContradictionsCheck().run(ctx)
    assert findings == []
