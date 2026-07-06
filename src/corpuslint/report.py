from __future__ import annotations

import html as _html
import json

from rich.console import Console
from rich.table import Table

from .models import Report


def render_json(report: Report) -> str:
    payload = {
        "score": report.score,
        "total_chunks": report.total_chunks,
        "findings": [
            {
                "check": f.check,
                "severity": f.severity.value,
                "message": f.message,
                "chunk_ids": list(f.chunk_ids),
                "source": f.source,
            }
            for f in report.findings
        ],
        "top_offenders": [[s, n] for s, n in report.top_offenders()],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_html(report: Report) -> str:
    rows = "".join(
        f"<tr><td>{_html.escape(f.check)}</td><td>{f.severity.value}</td>"
        f"<td>{_html.escape(f.message)}</td><td>{_html.escape(f.source)}</td></tr>"
        for f in report.findings
    )
    return (
        "<html><head><meta charset='utf-8'><title>corpuslint report</title></head>"
        f"<body><h1>corpuslint — Quality Score: {report.score}/100</h1>"
        f"<p>{report.total_chunks} chunks scanned, {len(report.findings)} findings.</p>"
        "<table border='1'><tr><th>check</th><th>severity</th><th>message</th><th>source</th></tr>"
        f"{rows}</table></body></html>"
    )


def render_terminal(report: Report) -> None:
    console = Console()
    console.print(f"[bold]corpuslint[/bold] — Quality Score: [bold]{report.score}/100[/bold]")
    console.print(f"{report.total_chunks} chunks scanned, {len(report.findings)} findings.\n")
    table = Table("check", "count")
    for check, findings in report.findings_by_check().items():
        table.add_row(check, str(len(findings)))
    console.print(table)
    if report.top_offenders():
        console.print("\n[bold]Top offenders:[/bold]")
        for source, count in report.top_offenders():
            console.print(f"  {source} → {count}")
