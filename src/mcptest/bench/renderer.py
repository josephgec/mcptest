"""Rich terminal renderers for benchmark comparison reports.

Three complementary views are provided:

``render_leaderboard``
    Ranked table — agents sorted by composite score with pass rate,
    duration, and a BEST badge for the top agent.

``render_metric_comparison``
    Pivot table — agents as rows, individual metric names as columns,
    cells colour-coded green/yellow/red.

``render_per_test_breakdown``
    Per-case grid — one row per (suite, case) pair, one column per agent,
    checkmark (✓) or cross (✗) in each cell.

All three use the same Rich colour conventions as the existing
``render_scorecard`` function in :mod:`mcptest.scorecard`:

* Score ≥ 0.8  → green
* Score ≥ 0.5  → yellow
* Score < 0.5  → red
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console

    from mcptest.bench.report import BenchmarkReport


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------


def _score_str(score: float) -> str:
    """Rich markup for a 0–1 score."""
    if score >= 0.8:
        return f"[green]{score:.3f}[/green]"
    if score >= 0.5:
        return f"[yellow]{score:.3f}[/yellow]"
    return f"[red]{score:.3f}[/red]"


def _rate_str(rate: float) -> str:
    """Rich markup for a 0–1 pass-rate percentage."""
    if rate >= 0.9:
        return f"[green]{rate:.0%}[/green]"
    if rate >= 0.6:
        return f"[yellow]{rate:.0%}[/yellow]"
    return f"[red]{rate:.0%}[/red]"


# ---------------------------------------------------------------------------
# Public renderers
# ---------------------------------------------------------------------------


def render_leaderboard(console: Console, report: BenchmarkReport) -> None:
    """Render a ranked agent leaderboard table.

    Columns: Rank · Agent · Score · Pass Rate · Duration · Verdict.
    The top agent receives a ``[bold green]BEST[/bold green]`` badge.
    """
    from rich.table import Table

    table = Table(title="Benchmark Leaderboard", show_lines=False)
    table.add_column("Rank", justify="right", style="bold")
    table.add_column("Agent", style="bold")
    table.add_column("Score", justify="right")
    table.add_column("Pass Rate", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Verdict", justify="center")

    for rank, summary in enumerate(report.summaries, 1):
        is_best = summary.agent == report.best_agent
        verdict = "[bold green]BEST[/bold green]" if is_best else "[dim]—[/dim]"
        table.add_row(
            str(rank),
            summary.agent,
            _score_str(summary.composite_score),
            _rate_str(summary.pass_rate),
            f"{summary.total_duration_s:.2f}s",
            verdict,
        )

    console.print(table)
    if report.best_agent:
        console.print(
            f"\nBest agent: [bold green]{report.best_agent}[/bold green]"
        )


def render_metric_comparison(console: Console, report: BenchmarkReport) -> None:
    """Render a pivot table of per-agent per-metric average scores.

    Rows = agents (in ranking order).
    Columns = metric names discovered from the entries.
    Cells are colour-coded by the green/yellow/red threshold.
    """
    from rich.table import Table

    if not report.summaries:
        console.print("[dim]No data to display.[/dim]")
        return

    # Collect all metric names preserving first-seen order.
    metric_names: list[str] = []
    seen: set[str] = set()
    for summary in report.summaries:
        for m in summary.per_metric:
            if m not in seen:
                metric_names.append(m)
                seen.add(m)

    table = Table(title="Metric Comparison", show_lines=False)
    table.add_column("Agent", style="bold")
    for m in metric_names:
        table.add_column(m, justify="right")

    for summary in report.summaries:
        row: list[str] = [summary.agent]
        for m in metric_names:
            score = summary.per_metric.get(m)
            if score is None:
                row.append("[dim]—[/dim]")
            else:
                row.append(_score_str(score))
        table.add_row(*row)

    console.print(table)


def render_per_test_breakdown(console: Console, report: BenchmarkReport) -> None:
    """Render a per-case pass/fail grid across agents.

    Rows = (suite, case) pairs in the order they were first seen.
    Columns = agent names in ranking order.
    Cells show ✓ (green) or ✗ (red); dash for missing data.
    """
    from rich.table import Table

    if not report.entries:
        console.print("[dim]No entries to display.[/dim]")
        return

    # Collect unique (suite, case) pairs preserving first-seen order.
    seen_cases: set[tuple[str, str]] = set()
    ordered_cases: list[tuple[str, str]] = []
    for entry in report.entries:
        key = (entry.suite, entry.case)
        if key not in seen_cases:
            ordered_cases.append(key)
            seen_cases.add(key)

    agents = report.ranking

    table = Table(title="Per-Test Breakdown", show_lines=False)
    table.add_column("Test", style="bold")
    for agent in agents:
        table.add_column(agent, justify="center")

    # Build lookup: (agent, suite, case) -> passed
    lookup: dict[tuple[str, str, str], bool] = {}
    for entry in report.entries:
        lookup[(entry.agent, entry.suite, entry.case)] = entry.passed

    for suite_name, case_name in ordered_cases:
        label = f"{suite_name} > {case_name}"
        row: list[str] = [label]
        for agent in agents:
            passed = lookup.get((agent, suite_name, case_name))
            if passed is None:
                row.append("[dim]—[/dim]")
            elif passed:
                row.append("[green]✓[/green]")
            else:
                row.append("[red]✗[/red]")
        table.add_row(*row)

    console.print(table)
