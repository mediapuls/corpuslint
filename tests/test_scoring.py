from corpuslint.config import Config
from corpuslint.models import Finding, Severity
from corpuslint.scoring import compute_score


def test_clean_corpus_scores_100():
    assert compute_score([], 10, Config()) == 100


def test_penalty_scales_with_findings_and_weights():
    findings = [Finding("exact_duplicates", Severity.ERROR, "d", ("a",), "s")]  # weight 1.0
    score = compute_score(findings, total_chunks=10, config=Config())
    assert score == 90  # 100 - (1.0/10*100)
