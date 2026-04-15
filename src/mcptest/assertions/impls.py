"""Concrete assertion implementations.

Each class is a dataclass carrying its configuration + a `yaml_key` class
attribute used by the YAML dispatch table. All assertions inherit from
`_AssertionBase` to get a consistent `.assert_()` method.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Iterable

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]

from mcptest.assertions.base import (
    AssertionResult,
    _AssertionBase,
    register_assertion,
)

if TYPE_CHECKING:
    from mcptest.runner.trace import Trace


def _result(
    *,
    passed: bool,
    name: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> AssertionResult:
    return AssertionResult(
        passed=passed, name=name, message=message, details=details or {}
    )


# ---------------------------------------------------------------------------
# Tool selection assertions
# ---------------------------------------------------------------------------


@register_assertion
@dataclass
class tool_called(_AssertionBase):  # noqa: N801 - public YAML name
    """Pass iff the agent invoked the named tool at least once."""

    tool: str
    yaml_key: ClassVar[str] = "tool_called"

    def check(self, trace: Trace) -> AssertionResult:
        count = trace.call_count(self.tool)
        return _result(
            passed=count >= 1,
            name=self.yaml_key,
            message=(
                f"tool {self.tool!r} was called {count} time(s)"
                if count >= 1
                else f"expected tool {self.tool!r} to be called at least once, but was not called"
            ),
            details={"tool": self.tool, "count": count},
        )


@register_assertion
@dataclass
class tool_not_called(_AssertionBase):  # noqa: N801
    """Pass iff the agent never invoked the named tool."""

    tool: str
    yaml_key: ClassVar[str] = "tool_not_called"

    def check(self, trace: Trace) -> AssertionResult:
        count = trace.call_count(self.tool)
        return _result(
            passed=count == 0,
            name=self.yaml_key,
            message=(
                f"tool {self.tool!r} was not called"
                if count == 0
                else f"expected tool {self.tool!r} to never be called, but was called {count} time(s)"
            ),
            details={"tool": self.tool, "count": count},
        )


@register_assertion
@dataclass
class tool_call_count(_AssertionBase):  # noqa: N801
    """Pass iff the named tool was called exactly `count` times."""

    tool: str
    count: int
    yaml_key: ClassVar[str] = "tool_call_count"

    def check(self, trace: Trace) -> AssertionResult:
        actual = trace.call_count(self.tool)
        return _result(
            passed=actual == self.count,
            name=self.yaml_key,
            message=(
                f"tool {self.tool!r} was called {actual} time(s) (expected {self.count})"
            ),
            details={"tool": self.tool, "expected": self.count, "actual": actual},
        )


@register_assertion
@dataclass
class max_tool_calls(_AssertionBase):  # noqa: N801
    """Pass iff the total number of tool calls is ≤ `limit`."""

    limit: int
    yaml_key: ClassVar[str] = "max_tool_calls"

    def check(self, trace: Trace) -> AssertionResult:
        actual = trace.total_tool_calls
        return _result(
            passed=actual <= self.limit,
            name=self.yaml_key,
            message=(
                f"total tool calls = {actual} (limit {self.limit})"
            ),
            details={"limit": self.limit, "actual": actual},
        )


# ---------------------------------------------------------------------------
# Parameter assertions
# ---------------------------------------------------------------------------


_MISSING: Any = object()


@register_assertion
@dataclass
class param_matches(_AssertionBase):  # noqa: N801
    """Pass iff at least one call to `tool` had `param` matching the condition.

    Conditions (choose exactly one): `value` (deep equality), `contains`
    (substring on the stringified value), or `regex` (Python regex search).
    """

    tool: str
    param: str
    value: Any = _MISSING
    contains: str | None = None
    regex: str | None = None
    call_index: int | None = None
    yaml_key: ClassVar[str] = "param_matches"

    def _condition_count(self) -> int:
        return sum(
            1
            for v in (self.value, self.contains, self.regex)
            if v is not _MISSING and v is not None
        )

    def check(self, trace: Trace) -> AssertionResult:
        if self._condition_count() != 1:
            return _result(
                passed=False,
                name=self.yaml_key,
                message="param_matches requires exactly one of value / contains / regex",
                details={"tool": self.tool, "param": self.param},
            )

        calls = trace.calls_to(self.tool)
        if self.call_index is not None:
            if 0 <= self.call_index < len(calls):
                calls = [calls[self.call_index]]
            else:
                return _result(
                    passed=False,
                    name=self.yaml_key,
                    message=(
                        f"no call #{self.call_index} to tool {self.tool!r} "
                        f"(only {len(calls)} call(s) observed)"
                    ),
                    details={"tool": self.tool, "param": self.param},
                )

        if not calls:
            return _result(
                passed=False,
                name=self.yaml_key,
                message=f"tool {self.tool!r} was never called, cannot check param",
                details={"tool": self.tool, "param": self.param},
            )

        checked_values: list[Any] = []
        for call in calls:
            if self.param not in call.arguments:
                continue
            actual = call.arguments[self.param]
            checked_values.append(actual)
            if self._matches(actual):
                return _result(
                    passed=True,
                    name=self.yaml_key,
                    message=f"{self.tool}.{self.param} matched",
                    details={"tool": self.tool, "param": self.param, "actual": actual},
                )

        return _result(
            passed=False,
            name=self.yaml_key,
            message=(
                f"{self.tool}.{self.param} did not match; observed values: {checked_values!r}"
                if checked_values
                else f"{self.tool}.{self.param} was not present on any call"
            ),
            details={"tool": self.tool, "param": self.param, "observed": checked_values},
        )

    def _matches(self, actual: Any) -> bool:
        if self.value is not _MISSING:
            return actual == self.value
        if self.contains is not None:
            return self.contains in str(actual)
        if self.regex is not None:
            try:
                return re.search(self.regex, str(actual)) is not None
            except re.error:
                return False
        return False  # pragma: no cover


@register_assertion
@dataclass
class param_schema_valid(_AssertionBase):  # noqa: N801
    """Pass iff every call to `tool` had arguments matching `schema`.

    The JSON schema is passed in explicitly — the assertion does not look up
    the mock fixture to find it, because the trace does not carry fixture
    schemas. Typically, tests bind this via the Python API when they want
    strict parameter validation.
    """

    tool: str
    schema: dict[str, Any]
    yaml_key: ClassVar[str] = "param_schema_valid"

    def check(self, trace: Trace) -> AssertionResult:
        if jsonschema is None:  # pragma: no cover
            return _result(
                passed=False,
                name=self.yaml_key,
                message="jsonschema package not available",
            )
        calls = trace.calls_to(self.tool)
        if not calls:
            return _result(
                passed=False,
                name=self.yaml_key,
                message=f"tool {self.tool!r} was never called",
                details={"tool": self.tool},
            )
        for i, call in enumerate(calls):
            try:
                jsonschema.validate(instance=call.arguments, schema=self.schema)
            except jsonschema.ValidationError as exc:
                return _result(
                    passed=False,
                    name=self.yaml_key,
                    message=f"{self.tool} call #{i} failed schema: {exc.message}",
                    details={"tool": self.tool, "call_index": i, "arguments": call.arguments},
                )
        return _result(
            passed=True,
            name=self.yaml_key,
            message=f"all {len(calls)} call(s) to {self.tool!r} matched schema",
            details={"tool": self.tool, "calls_checked": len(calls)},
        )


# ---------------------------------------------------------------------------
# Ordering / trajectory assertions
# ---------------------------------------------------------------------------


@register_assertion
@dataclass
class tool_order(_AssertionBase):  # noqa: N801
    """Pass iff `sequence` appears as a contiguous subsequence of the tool calls.

    Ignores extra calls before or after. Use `trajectory_matches` for strict
    full-sequence equality.
    """

    sequence: list[str]
    yaml_key: ClassVar[str] = "tool_order"

    def check(self, trace: Trace) -> AssertionResult:
        observed = trace.tool_names
        seq = list(self.sequence)
        if not seq:
            return _result(
                passed=True,
                name=self.yaml_key,
                message="empty sequence trivially matches",
                details={"observed": observed},
            )
        for start in range(len(observed) - len(seq) + 1):
            if observed[start : start + len(seq)] == seq:
                return _result(
                    passed=True,
                    name=self.yaml_key,
                    message=f"sequence {seq!r} found starting at index {start}",
                    details={"sequence": seq, "observed": observed, "index": start},
                )
        return _result(
            passed=False,
            name=self.yaml_key,
            message=f"sequence {seq!r} not found in observed trajectory {observed!r}",
            details={"sequence": seq, "observed": observed},
        )


@register_assertion
@dataclass
class trajectory_matches(_AssertionBase):  # noqa: N801
    """Pass iff the full ordered list of tool calls equals `expected`."""

    expected: list[str]
    yaml_key: ClassVar[str] = "trajectory_matches"

    def check(self, trace: Trace) -> AssertionResult:
        observed = trace.tool_names
        passed = observed == list(self.expected)
        return _result(
            passed=passed,
            name=self.yaml_key,
            message=(
                f"trajectory matched: {observed!r}"
                if passed
                else f"trajectory differed: expected {list(self.expected)!r}, got {observed!r}"
            ),
            details={"expected": list(self.expected), "observed": observed},
        )


# ---------------------------------------------------------------------------
# Performance / output assertions
# ---------------------------------------------------------------------------


@register_assertion
@dataclass
class completes_within_s(_AssertionBase):  # noqa: N801
    """Pass iff `trace.duration_s <= seconds`."""

    seconds: float
    yaml_key: ClassVar[str] = "completes_within_s"

    def check(self, trace: Trace) -> AssertionResult:
        passed = trace.duration_s <= self.seconds
        return _result(
            passed=passed,
            name=self.yaml_key,
            message=(
                f"ran in {trace.duration_s:.3f}s (budget {self.seconds}s)"
            ),
            details={"limit": self.seconds, "actual": trace.duration_s},
        )


@register_assertion
@dataclass
class output_contains(_AssertionBase):  # noqa: N801
    """Pass iff `needle` is a substring of `trace.output`."""

    needle: str
    case_sensitive: bool = True
    yaml_key: ClassVar[str] = "output_contains"

    def check(self, trace: Trace) -> AssertionResult:
        haystack = trace.output
        if self.case_sensitive:
            passed = self.needle in haystack
        else:
            passed = self.needle.lower() in haystack.lower()
        return _result(
            passed=passed,
            name=self.yaml_key,
            message=(
                f"output contained {self.needle!r}"
                if passed
                else f"output did not contain {self.needle!r}"
            ),
            details={"needle": self.needle, "output": haystack},
        )


@register_assertion
@dataclass
class output_matches(_AssertionBase):  # noqa: N801
    """Pass iff `trace.output` matches the given regex (via `re.search`)."""

    pattern: str
    yaml_key: ClassVar[str] = "output_matches"

    def check(self, trace: Trace) -> AssertionResult:
        try:
            match = re.search(self.pattern, trace.output)
        except re.error as exc:
            return _result(
                passed=False,
                name=self.yaml_key,
                message=f"invalid regex {self.pattern!r}: {exc}",
                details={"pattern": self.pattern},
            )
        return _result(
            passed=match is not None,
            name=self.yaml_key,
            message=(
                f"output matched pattern {self.pattern!r}"
                if match
                else f"output did not match pattern {self.pattern!r}"
            ),
            details={"pattern": self.pattern, "output": trace.output},
        )


# ---------------------------------------------------------------------------
# Error-handling assertions
# ---------------------------------------------------------------------------


@register_assertion
@dataclass
class no_errors(_AssertionBase):  # noqa: N801
    """Pass iff no tool call produced an error result."""

    yaml_key: ClassVar[str] = "no_errors"
    _placeholder: bool = False  # lets YAML accept `no_errors: true`

    def __init__(self, arg: Any = None) -> None:
        # YAML emits `{no_errors: true}` or `{no_errors: null}`; accept both.
        self._placeholder = bool(arg)

    def check(self, trace: Trace) -> AssertionResult:
        errors = trace.errors()
        return _result(
            passed=len(errors) == 0,
            name=self.yaml_key,
            message=(
                f"no tool-call errors observed"
                if not errors
                else f"{len(errors)} tool-call error(s): "
                + ", ".join(f"{e.tool}({e.error})" for e in errors)
            ),
            details={"error_count": len(errors)},
        )


@register_assertion
@dataclass
class error_handled(_AssertionBase):  # noqa: N801
    """Pass iff the named error was observed AND the agent still completed.

    Expects:
    - at least one tool call with `error` matching either the full message
      or the error name (we allow both to keep YAML ergonomic);
    - `trace.exit_code == 0`.
    """

    error: str
    yaml_key: ClassVar[str] = "error_handled"

    def check(self, trace: Trace) -> AssertionResult:
        matching = [
            c
            for c in trace.tool_calls
            if c.is_error
            and (
                self.error in (c.error or "")
                or (c.error or "") == self.error
            )
        ]
        if not matching:
            return _result(
                passed=False,
                name=self.yaml_key,
                message=f"expected error {self.error!r} was never raised by a tool",
                details={"error": self.error},
            )
        if not trace.succeeded:
            return _result(
                passed=False,
                name=self.yaml_key,
                message=(
                    f"error {self.error!r} raised but agent did not complete successfully "
                    f"(exit_code={trace.exit_code}, agent_error={trace.agent_error})"
                ),
                details={"error": self.error, "exit_code": trace.exit_code},
            )
        return _result(
            passed=True,
            name=self.yaml_key,
            message=f"error {self.error!r} was raised and the agent recovered",
            details={"error": self.error, "error_calls": len(matching)},
        )


# Keep dataclass import happy for py3.10 even if we don't use field() elsewhere.
_ = field
_ = Iterable
