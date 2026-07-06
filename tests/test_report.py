import json
from corpuslint.models import Finding, Severity, Report
from corpuslint.report import render_json, render_html


def _report():
    return Report(total_chunks=3, score=80, findings=[
        Finding("exact_duplicates", Severity.ERROR, "dup", ("a", "b"), "docs/x.md"),
    ])


def test_render_json_is_stable_and_complete():
    data = json.loads(render_json(_report()))
    assert data["score"] == 80
    assert data["findings"][0]["check"] == "exact_duplicates"
    assert data["top_offenders"] == [["docs/x.md", 1]]


def test_render_html_contains_score_and_findings():
    html = render_html(_report())
    assert "80" in html and "exact_duplicates" in html and "<html" in html.lower()
