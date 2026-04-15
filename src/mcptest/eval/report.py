"""Evaluation report aggregation and Rich rendering.

:class:`EvalSummary` aggregates a list of :class:`~mcptest.eval.grader.EvalResult`
objects produced by a :class:`~mcptest.eval.grader.Grader` run across multiple
test cases.  It mirrors the patterns established by
:class:`~mcptest.bench.report.BenchmarkReport` and
:class:`~mcptest.coverage.engine.CoverageReport`.

Typical usage::

    from mcptest.eval import Grader, aggregate_results, load_rubric

    rubric = load_rubric(Path("rubrics/booking.yaml"))
    grader = Grader(rubric)

    results = [grader.grade(trace.output) for trace in traces]
    summary = aggregate_results(results)

    from rich.console import Console
    render_eval_report(Console(), summary)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.table import Table

from mcptest.eval.grader import EvalResult


@dataclass
class EvalSummary:
    """Aggregated evaluation results across multiple test cases.

    Attributes:
        total_cases: Number of :class:`~mcptest.eval.grader.EvalResult` objects
            aggregated.
        passed_cases: Number of results where ``passed`` was ``True``.
        pass_rate: ``passed_cases / total_cases`` (0.0 if no cases).
        mean_composite: Average ``composite_score`` across all results.
        per_criterion: Average score per criterion name.
        results: The original :class:`~mcptest.eval.grader.EvalResult` list.
    """

    total_cases: int
    passed_cases: int
    pass_rate: float
    mean_composite: float
    per_criterion: dict[str, float] = field(default_factory=dict)
    results: list[EvalResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "pass_rate": round(self.pass_rate, 4),
            "mean_composite": round(self.mean_composite, 4),
            "per_criterion": {k: round(v, 4) for k, v in self.per_criterion.items()},
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


def aggregate_results(results: list[EvalResult]) -> EvalSummary:
    """Aggregate a list of :class:`~mcptest.eval.grader.EvalResult` objects.

    Args:
        results: Evaluation results from one or more graded traces.

    Returns:
        An :class:`EvalSummary` with aggregate statistics.
    """
    if not results:
        return EvalSummary(
            total_cases=0,
            passed_cases=0,
            pass_rate=0.0,
            mean_composite=0.0,
            per_criterion={},
            results=[],
        )

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pass_rate = passed / total
    mean_composite = sum(r.composite_score for r in results) / total

    # Average score per criterion name across all results.
    criterion_totals: dict[str, list[float]] = {}
    for result in results:
        for cr in result.criterion_results:
            criterion_totals.setdefault(cr.criterion, []).append(cr.score)
    per_criterion = {
        name: sum(scores) / len(scores)
        for name, scores in criterion_totals.items()
    }

    return EvalSummary(
        total_cases=total,
        passed_cases=passed,
        pass_rate=pass_rate,
        mean_composite=mean_composite,
        per_criterion=per_criterion,
        results=results,
    )


def render_eval_report(console: Console, summary: EvalSummary) -> None:
    """Render an evaluation summary as Rich tables.

    Prints:
    1. A per-criterion table: Criterion | Avg Score | Pass Rate | Verdict.
    2. An overall summary line with composite score and pass rate.

    Args:
        console: Rich :class:`~rich.console.Console` to render into.
        summary: The aggregated evaluation summary to display.
    """
    if summary.total_cases == 0:
        console.print("[yellow]no evaluation results to display[/yellow]")
        return

    rubric_name = summary.results[0].rubric if summary.results else "unknown"
    console.print(f"\n[bold]Evaluation Report[/bold] — rubric: [cyan]{rubric_name}[/cyan]\n")

    # Build per-criterion statistics across all results.
    criterion_pass_counts: dict[str, int] = {}
    criterion_total_counts: dict[str, int] = {}
    for result in summary.results:
        for cr in result.criterion_results:
            criterion_total_counts.setdefault(cr.criterion, 0)
            criterion_pass_counts.setdefault(cr.criterion, 0)
            criterion_total_counts[cr.criterion] += 1
            if cr.passed:
                criterion_pass_counts[cr.criterion] += 1

    table = Table(show_header=True, header_style="bold")
    table.add_column("Criterion", style="cyan")
    table.add_column("Avg Score", justify="right")
    table.add_column("Pass Rate", justify="right")
    table.add_column("Verdict", justify="center")

    for name, avg_score in summary.per_criterion.items():
        total_c = criterion_total_counts.get(name, 0)
        passed_c = criterion_pass_counts.get(name, 0)
        crit_pass_rate = passed_c / total_c if total_c > 0 else 0.0
        verdict = "[green]PASS[/green]" if crit_pass_rate == 1.0 else (
            "[yellow]PARTIAL[/yellow]" if crit_pass_rate > 0.0 else "[red]FAIL[/red]"
        )
        table.add_row(
            name,
            f"{avg_score:.3f}",
            f"{crit_pass_rate:.1%}",
            verdict,
        )

    console.print(table)

    # Overall summary line.
    overall_color = "green" if summary.pass_rate == 1.0 else (
        "yellow" if summary.pass_rate > 0.0 else "red"
    )
    console.print(
        f"\n[bold]Overall:[/bold] "
        f"[{overall_color}]{summary.passed_cases}/{summary.total_cases} passed[/{overall_color}] "
        f"({summary.pass_rate:.1%}) — "
        f"composite score [bold]{summary.mean_composite:.3f}[/bold]\n"
    )
