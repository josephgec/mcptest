"""Shared types and plumbing for trace metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from mcptest.fixtures.models import Fixture
    from mcptest.runner.trace import Trace


@dataclass(frozen=True)
class MetricResult:
    """The outcome of evaluating one metric against a trace."""

    name: str
    score: float  # 0.0–1.0 normalized
    label: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score,
            "label": self.label,
            "details": self.details,
        }


class TraceMetric(Protocol):
    """Everything a metric needs to play nicely with the runner and CLI."""

    name: ClassVar[str]
    label: ClassVar[str]

    def compute(
        self,
        trace: Trace,
        *,
        reference: Trace | None = None,
        fixtures: list[Fixture] | None = None,
    ) -> MetricResult: ...


METRICS: dict[str, type] = {}


def register_metric(cls: type) -> type:
    """Decorator — add a metric class to the dispatch table."""
    key = getattr(cls, "name", None)
    if not key:
        raise TypeError(f"{cls.__name__} is missing a name class attribute")
    if key in METRICS:
        raise ValueError(f"metric {key!r} already registered")
    METRICS[key] = cls
    return cls


class _MetricBase:
    """Mixin providing shared boilerplate for every metric."""

    name: ClassVar[str] = ""
    label: ClassVar[str] = ""

    def compute(
        self,
        trace: Trace,
        *,
        reference: Trace | None = None,
        fixtures: list[Fixture] | None = None,
    ) -> MetricResult:  # pragma: no cover
        raise NotImplementedError


#: Public base class alias for plugin authors.
Metric = _MetricBase


def compute_all(
    trace: Trace,
    *,
    reference: Trace | None = None,
    fixtures: list[Fixture] | None = None,
) -> list[MetricResult]:
    """Run every registered metric and return results."""
    results: list[MetricResult] = []
    for cls in METRICS.values():
        instance = cls()
        results.append(instance.compute(trace, reference=reference, fixtures=fixtures))
    return results
