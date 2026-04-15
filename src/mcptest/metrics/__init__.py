"""Quantitative metrics for MCP agent traces.

Six MCP-protocol-aware metrics ship here, each producing a score between
0.0 and 1.0. Unlike assertions (binary pass/fail), metrics give a continuous
quality signal useful for agent comparison and regression tracking.

Python:

    from mcptest.metrics import compute_all, tool_efficiency

    results = compute_all(trace)
    for r in results:
        print(f"{r.label}: {r.score:.2f}")

    # Or compute a single metric directly:
    result = tool_efficiency().compute(trace)
"""

from __future__ import annotations

from mcptest.metrics.base import (
    METRICS,
    MetricResult,
    TraceMetric,
    _MetricBase,
    compute_all,
    register_metric,
)
from mcptest.metrics.impls import (
    error_recovery_rate,
    redundancy,
    schema_compliance,
    tool_coverage,
    tool_efficiency,
    trajectory_similarity,
)

__all__ = [
    "METRICS",
    "MetricResult",
    "TraceMetric",
    "compute_all",
    "register_metric",
    "error_recovery_rate",
    "redundancy",
    "schema_compliance",
    "tool_coverage",
    "tool_efficiency",
    "trajectory_similarity",
]
