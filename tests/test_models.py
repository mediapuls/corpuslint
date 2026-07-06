from corpuslint.models import Severity, Chunk, Finding, Report


def test_top_offenders_ranks_sources_by_finding_count():
    findings = [
        Finding("exact_duplicates", Severity.ERROR, "dup", ("a", "b"), source="docs/x.md"),
        Finding("low_information", Severity.WARNING, "thin", ("c",), source="docs/x.md"),
        Finding("chunk_size", Severity.WARNING, "big", ("d",), source="docs/y.md"),
    ]
    report = Report(total_chunks=4, findings=findings, score=70)
    assert report.top_offenders(1) == [("docs/x.md", 2)]
    assert report.findings_by_check()["exact_duplicates"][0].chunk_ids == ("a", "b")
