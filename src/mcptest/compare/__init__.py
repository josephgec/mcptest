"""Metric regression comparison engine for mcptest traces.

Usage::

    from mcptest.compare import compare_traces

    report = compare_traces(base_trace, head_trace)
    if not report.overall_passed:
        for delta in report.regressions:
            print(f"{delta.name}: {delta.base_score:.3f} → {delta.head_score:.3f}")
"""

from __future__ import annotations

from mcptest.compare.engine import (
    DEFAULT_THRESHOLDS,
    ComparisonReport,
    MetricDelta,
    compare_traces,
)

__all__ = [
    "DEFAULT_THRESHOLDS",
    "ComparisonReport",
    "MetricDelta",
    "compare_traces",
]
