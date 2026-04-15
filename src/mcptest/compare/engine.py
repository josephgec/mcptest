"""Metric regression comparison between two traces.

Computes all registered metrics for a base and head trace, pairs them up,
and flags per-metric regressions based on configurable thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mcptest.metrics import compute_all

if TYPE_CHECKING:
    from mcptest.fixtures.models import Fixture
    from mcptest.runner.trace import Trace


# Default regression threshold per metric (score drop that triggers a flag).
DEFAULT_THRESHOLDS: dict[str, float] = {
    "tool_efficiency": 0.1,
    "redundancy": 0.1,
    "error_recovery_rate": 0.1,
    "trajectory_similarity": 0.1,
    "schema_compliance": 0.1,
    "tool_coverage": 0.1,
}


@dataclass
class MetricDelta:
    """The change in one metric score between a base and head trace."""

    name: str
    label: str
    base_score: float
    head_score: float
    threshold: float = 0.1
    # Computed by __post_init__ — not accepted in __init__.
    delta: float = field(default=0.0, init=False)
    regressed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.delta = self.head_score - self.base_score
        # Round to 9 decimal places before comparing so that floating-point
        # arithmetic artefacts (e.g. 0.7 - 0.8 == -0.10000000000000009) don't
        # cause a boundary-equal drop to spuriously trigger a regression.
        self.regressed = round(self.delta, 9) < round(-self.threshold, 9)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "base_score": self.base_score,
            "head_score": self.head_score,
            "delta": self.delta,
            "regressed": self.regressed,
            "threshold": self.threshold,
        }


@dataclass
class ComparisonReport:
    """Full comparison result between a base and head trace."""

    base_trace_id: str
    head_trace_id: str
    deltas: list[MetricDelta]

    @property
    def overall_passed(self) -> bool:
        """True if no metric regressed beyond its threshold."""
        return not any(d.regressed for d in self.deltas)

    @property
    def regressions(self) -> list[MetricDelta]:
        """Metrics that dropped beyond their threshold."""
        return [d for d in self.deltas if d.regressed]

    @property
    def improvements(self) -> list[MetricDelta]:
        """Metrics that improved by at least 0.05."""
        return [d for d in self.deltas if d.delta >= 0.05]

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_trace_id": self.base_trace_id,
            "head_trace_id": self.head_trace_id,
            "deltas": [d.to_dict() for d in self.deltas],
            "overall_passed": self.overall_passed,
            "regression_count": len(self.regressions),
        }


def compare_traces(
    base: Trace,
    head: Trace,
    *,
    thresholds: dict[str, float] | None = None,
    fixtures: list[Fixture] | None = None,
) -> ComparisonReport:
    """Compare two traces by computing and diffing all registered metrics.

    Args:
        base: The baseline trace (reference).
        head: The head (current) trace to compare against the baseline.
        thresholds: Per-metric regression thresholds. Any key not present falls
            back to the corresponding value in ``DEFAULT_THRESHOLDS`` (0.1).
        fixtures: Optional fixtures forwarded to schema_compliance /
            tool_coverage metrics.

    Returns:
        ``ComparisonReport`` with per-metric deltas and overall regression status.
    """
    effective: dict[str, float] = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        effective.update(thresholds)

    base_results = compute_all(base, fixtures=fixtures)
    head_results = compute_all(head, fixtures=fixtures)

    base_by_name = {r.name: r for r in base_results}
    head_by_name = {r.name: r for r in head_results}

    all_names = sorted(set(base_by_name) | set(head_by_name))

    deltas: list[MetricDelta] = []
    for name in all_names:
        base_r = base_by_name.get(name)
        head_r = head_by_name.get(name)
        if base_r is None or head_r is None:
            # Metric only present in one trace — skip rather than guess.
            continue
        threshold = effective.get(name, 0.1)
        deltas.append(
            MetricDelta(
                name=name,
                label=base_r.label,
                base_score=base_r.score,
                head_score=head_r.score,
                threshold=threshold,
            )
        )

    return ComparisonReport(
        base_trace_id=base.trace_id,
        head_trace_id=head.trace_id,
        deltas=deltas,
    )
