"""Conformance report renderer.

Provides ``render_conformance_report`` which produces either a human-readable
Rich table (grouped by section) or machine-readable JSON.
"""

from __future__ import annotations

import json
from typing import Any

from mcptest.conformance.check import ConformanceResult, Severity


def _severity_color(severity: Severity) -> str:
    if severity == Severity.MUST:
        return "red"
    if severity == Severity.SHOULD:
        return "yellow"
    return "blue"


def _status_cell(result: ConformanceResult) -> str:
    if result.skipped:
        return "[dim]SKIP[/dim]"
    return "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"


def render_conformance_report(
    results: list[ConformanceResult],
    *,
    as_json: bool = False,
    console: Any = None,
) -> str:
    """Render conformance results as a Rich table or JSON string.

    Args:
        results: The list of ``ConformanceResult`` objects to render.
        as_json: When True, return machine-readable JSON instead of a table.
        console: A ``rich.console.Console`` instance.  When ``None`` and
            ``as_json=False``, a new Console targeting stdout is used.

    Returns:
        The rendered string (table markup or JSON).
    """
    if as_json:
        return _as_json(results)
    return _as_table(results, console=console)


def _as_json(results: list[ConformanceResult]) -> str:
    must_failures = [
        r for r in results if not r.passed and not r.skipped and r.check.severity == Severity.MUST
    ]
    total = len(results)
    passed = sum(1 for r in results if r.passed and not r.skipped)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    pass_rate = passed / max(total - skipped, 1)

    return json.dumps(
        {
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "must_failures": len(must_failures),
                "pass_rate": round(pass_rate, 4),
            },
            "results": [r.to_dict() for r in results],
        },
        indent=2,
        default=str,
    )


def _as_table(
    results: list[ConformanceResult], *, console: Any = None
) -> str:
    from io import StringIO

    from rich.console import Console
    from rich.table import Table

    buf = StringIO()
    out = Console(file=buf, highlight=False) if console is None else console

    # Group results by section for display
    sections: dict[str, list[ConformanceResult]] = {}
    for r in results:
        sections.setdefault(r.check.section, []).append(r)

    for section_name, section_results in sections.items():
        table = Table(
            title=f"[bold]{section_name}[/bold]",
            show_lines=False,
            show_header=True,
        )
        table.add_column("ID", style="bold dim", width=10)
        table.add_column("Severity", justify="center", width=9)
        table.add_column("Check", ratio=1)
        table.add_column("Status", justify="center", width=6)
        table.add_column("Message", ratio=2)

        for r in section_results:
            color = _severity_color(r.check.severity)
            sev_cell = f"[{color}]{r.check.severity.value}[/{color}]"
            table.add_row(
                r.check.id,
                sev_cell,
                r.check.name,
                _status_cell(r),
                r.message,
            )

        out.print(table)

    # Summary footer
    must_failures = [
        r for r in results if not r.passed and not r.skipped and r.check.severity == Severity.MUST
    ]
    total = len(results)
    passed = sum(1 for r in results if r.passed and not r.skipped)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)

    out.print(
        f"\n[bold]{passed}/{total - skipped}[/bold] checks passed"
        + (f", [dim]{skipped} skipped[/dim]" if skipped else "")
        + (f"  —  [red]{len(must_failures)} MUST failure(s)[/red]" if must_failures else "")
    )

    if console is None:
        return buf.getvalue()
    return ""
