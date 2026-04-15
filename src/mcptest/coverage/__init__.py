"""MCP fixture coverage analysis.

Public API re-exported from ``mcptest.coverage.engine``.

Typical usage::

    from mcptest.coverage import analyze_coverage

    report = analyze_coverage(fixtures, traces, test_cases=cases)
    print(f"Overall: {report.overall_score:.0%}")
"""

from mcptest.coverage.engine import (
    CoverageReport,
    ErrorCoverageItem,
    ResponseCoverageItem,
    ToolCoverageItem,
    analyze_coverage,
)

__all__ = [
    "CoverageReport",
    "ErrorCoverageItem",
    "ResponseCoverageItem",
    "ToolCoverageItem",
    "analyze_coverage",
]
