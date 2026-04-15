"""Agent Scorecard — weighted quality report card for MCP agent traces.

The scorecard aggregates all (or a subset of) metrics into a single weighted
composite score, renders a Rich table, and exposes an exit-code-friendly
``composite_passed`` flag for CI gating.

Typical usage via CLI::

    mcptest scorecard trace.json
    mcptest scorecard trace.json --fail-under 0.75
    mcptest scorecard trace.json --config scorecard.yaml --json

Programmatic usage::

    from mcptest.scorecard import Scorecard, ScorecardConfig

    config = ScorecardConfig(
        thresholds={"tool_efficiency": 0.8, "redundancy": 0.7},
        weights={"tool_efficiency": 0.5, "redundancy": 0.5},
        composite_threshold=0.75,
    )
    card = Scorecard.from_trace(trace, config)
    print(card.composite_score, card.composite_passed)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcptest.fixtures.models import Fixture
    from mcptest.runner.trace import Trace


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class ScorecardConfig:
    """Thresholds and weights for each metric in the scorecard.

    Any metric not listed in ``weights`` is included with weight 1.0 (equal
    weighting).  Any metric not listed in ``thresholds`` uses the
    ``default_threshold``.
    """

    thresholds: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    composite_threshold: float = 0.75
    default_threshold: float = 0.7

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScorecardConfig:
        return cls(
            thresholds=data.get("thresholds", {}),
            weights=data.get("weights", {}),
            composite_threshold=float(data.get("composite_threshold", 0.75)),
            default_threshold=float(data.get("default_threshold", 0.7)),
        )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ScorecardEntry:
    """One row of the scorecard — a single metric's evaluation."""

    name: str
    label: str
    score: float
    threshold: float
    weight: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "score": round(self.score, 4),
            "threshold": self.threshold,
            "weight": self.weight,
            "passed": self.passed,
        }


@dataclass
class Scorecard:
    """Aggregated quality report card for one agent trace.

    Attributes:
        entries: One entry per evaluated metric.
        composite_score: Weighted average of all metric scores.
        composite_passed: True iff composite_score >= config.composite_threshold.
        trace_id: Identifier from the source trace.
    """

    entries: list[ScorecardEntry]
    composite_score: float
    composite_passed: bool
    trace_id: str

    @classmethod
    def from_trace(
        cls,
        trace: Trace,
        config: ScorecardConfig | None = None,
        *,
        fixtures: list[Fixture] | None = None,
    ) -> Scorecard:
        """Compute all registered metrics and build a Scorecard."""
        from mcptest.metrics.base import METRICS

        if config is None:
            config = ScorecardConfig()

        entries: list[ScorecardEntry] = []
        for metric_name, metric_cls in METRICS.items():
            instance = metric_cls()
            result = instance.compute(trace, fixtures=fixtures)
            threshold = config.thresholds.get(metric_name, config.default_threshold)
            weight = config.weights.get(metric_name, 1.0)
            entries.append(
                ScorecardEntry(
                    name=metric_name,
                    label=result.label,
                    score=result.score,
                    threshold=threshold,
                    weight=weight,
                    passed=result.score >= threshold,
                )
            )

        total_weight = sum(e.weight for e in entries)
        if total_weight > 0:
            composite = sum(e.score * e.weight for e in entries) / total_weight
        else:
            composite = 0.0

        composite_passed = composite >= config.composite_threshold

        return cls(
            entries=entries,
            composite_score=composite,
            composite_passed=composite_passed,
            trace_id=getattr(trace, "trace_id", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "composite_score": round(self.composite_score, 4),
            "composite_passed": self.composite_passed,
            "entries": [e.to_dict() for e in self.entries],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Rich rendering
# ---------------------------------------------------------------------------


def render_scorecard(console: Any, scorecard: Scorecard) -> None:  # console: Console
    """Render a Scorecard as a Rich table with a composite footer."""
    from rich.table import Table

    table = Table(title=f"mcptest scorecard  [dim](trace: {scorecard.trace_id})[/dim]", show_lines=False)
    table.add_column("Metric", style="bold")
    table.add_column("Score", justify="right")
    table.add_column("Threshold", justify="right")
    table.add_column("Weight", justify="right")
    table.add_column("Status", justify="center")

    for entry in scorecard.entries:
        if entry.score >= 0.8:
            score_str = f"[green]{entry.score:.3f}[/green]"
        elif entry.score >= 0.5:
            score_str = f"[yellow]{entry.score:.3f}[/yellow]"
        else:
            score_str = f"[red]{entry.score:.3f}[/red]"

        status = "[green]PASS[/green]" if entry.passed else "[red]FAIL[/red]"
        table.add_row(
            entry.name,
            score_str,
            f"{entry.threshold:.2f}",
            f"{entry.weight:.2f}",
            status,
        )

    console.print(table)

    # Composite footer
    comp = scorecard.composite_score
    if comp >= 0.8:
        comp_str = f"[green]{comp:.3f}[/green]"
    elif comp >= 0.5:
        comp_str = f"[yellow]{comp:.3f}[/yellow]"
    else:
        comp_str = f"[red]{comp:.3f}[/red]"

    verdict = "[green]PASSED[/green]" if scorecard.composite_passed else "[red]FAILED[/red]"
    console.print(f"\nComposite score: {comp_str}  —  {verdict}")
