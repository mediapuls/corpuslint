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


class CountingLLM:
    def __init__(self, answer: str = "YES"):
        self.answer = answer
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return self.answer


def _all_similar_ctx(n: int, max_pairs: int, llm):
    chunks = [Chunk(str(i), f"passage {i}", "s") for i in range(n)]
    # near-parallel vectors -> every pair is above the similarity prefilter
    embeddings = [[1.0, i * 1e-4] for i in range(n)]
    cfg = Config(use_llm=True, llm_max_pairs=max_pairs)
    return CheckContext(chunks, embeddings, cfg, llm=llm)


def test_cost_cap_limits_llm_calls_and_warns(capsys):
    n = 5  # C(5,2) = 10 candidate pairs
    llm = CountingLLM("YES")
    ctx = _all_similar_ctx(n, max_pairs=3, llm=llm)
    findings = ContradictionsCheck().run(ctx)
    assert llm.calls == 3  # capped
    assert len(findings) == 3  # one per evaluated YES pair
    err = capsys.readouterr().err
    assert "skipped" in err.lower()
    assert "7" in err  # 10 candidate pairs - 3 evaluated


def test_no_warning_when_under_cap(capsys):
    llm = CountingLLM("NO")
    ctx = _all_similar_ctx(3, max_pairs=100, llm=llm)  # 3 pairs < cap
    ContradictionsCheck().run(ctx)
    assert llm.calls == 3
    assert capsys.readouterr().err == ""


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
