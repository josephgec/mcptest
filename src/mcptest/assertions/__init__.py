"""Assertion library for MCP agent traces.

Every assertion is a dataclass with a `check(trace) -> AssertionResult` method
and can be expressed in either Python code or YAML test files. Thirteen core
assertions ship with the library — see `ASSERTIONS` for the full table.

Metric-gated assertions let you use any computed metric as a pass/fail gate:

    - metric_above: {metric: tool_efficiency, threshold: 0.8}
    - metric_below: {metric: redundancy, threshold: 0.2}

Combinators express complex boolean logic over other assertions:

    - all_of:
        - tool_called: create_issue
        - max_tool_calls: 5
    - any_of:
        - tool_called: create_issue
        - output_contains: created
    - none_of:
        - tool_called: delete_all

The `weighted_score` assertion gates on a composite quality score:

    - weighted_score:
        threshold: 0.75
        weights:
          tool_efficiency: 0.3
          redundancy: 0.2
          error_recovery_rate: 0.5

Failing a `check` is always represented as `AssertionResult(passed=False)`;
call `assert_(trace)` on an assertion (or `assert_all(...)` on a list) to
raise `McpTestAssertionError` instead for pytest-style use.
"""

from __future__ import annotations

from mcptest.assertions.base import (
    ASSERTIONS,
    AssertionResult,
    McpTestAssertionError,
    TraceAssertion,
    assert_all,
    check_all,
    parse_assertion,
    parse_assertions,
)
from mcptest.assertions.combinators import (
    all_of,
    any_of,
    none_of,
    weighted_score,
)
from mcptest.assertions.impls import (
    completes_within_s,
    error_handled,
    max_tool_calls,
    metric_above,
    metric_below,
    no_errors,
    output_contains,
    output_matches,
    param_matches,
    param_schema_valid,
    tool_call_count,
    tool_called,
    tool_not_called,
    tool_order,
    trajectory_matches,
)

__all__ = [
    "ASSERTIONS",
    "AssertionResult",
    "McpTestAssertionError",
    "TraceAssertion",
    "all_of",
    "any_of",
    "assert_all",
    "check_all",
    "completes_within_s",
    "error_handled",
    "max_tool_calls",
    "metric_above",
    "metric_below",
    "no_errors",
    "none_of",
    "output_contains",
    "output_matches",
    "param_matches",
    "param_schema_valid",
    "parse_assertion",
    "parse_assertions",
    "tool_call_count",
    "tool_called",
    "tool_not_called",
    "tool_order",
    "trajectory_matches",
    "weighted_score",
]
