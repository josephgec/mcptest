"""Assertion library for MCP agent traces.

Every assertion is a dataclass with a `check(trace) -> AssertionResult` method
and can be expressed in either Python code or YAML test files. Thirteen core
assertions ship in this session — see `ASSERTIONS` for the full table.

Python:

    from mcptest.assertions import tool_called, max_tool_calls, check_all

    results = check_all([
        tool_called("create_issue"),
        max_tool_calls(3),
    ], trace)

YAML (parsed by `parse_assertion`):

    - tool_called: create_issue
    - max_tool_calls: 3
    - param_matches: { tool: create_issue, param: title, contains: "500" }

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
from mcptest.assertions.impls import (
    completes_within_s,
    error_handled,
    max_tool_calls,
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
    "assert_all",
    "check_all",
    "completes_within_s",
    "error_handled",
    "max_tool_calls",
    "no_errors",
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
]
