"""Shared types and plumbing for trace assertions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from mcptest.runner.trace import Trace


class McpTestAssertionError(AssertionError):
    """Raised when an assertion fails and the caller opted into raising."""

    def __init__(self, result: AssertionResult) -> None:
        super().__init__(result.message)
        self.result = result


@dataclass
class AssertionResult:
    """The outcome of evaluating one assertion against a trace."""

    passed: bool
    name: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            "details": self.details,
        }


class TraceAssertion(Protocol):
    """Everything an assertion needs to play nicely with the runner and CLI."""

    yaml_key: ClassVar[str]

    def check(self, trace: Trace) -> AssertionResult: ...

    def assert_(self, trace: Trace) -> None: ...


ASSERTIONS: dict[str, type] = {}


def register_assertion(cls: type) -> type:
    """Decorator — add an assertion class to the YAML dispatch table."""
    key = getattr(cls, "yaml_key", None)
    if not key:
        raise TypeError(f"{cls.__name__} is missing a yaml_key class attribute")
    if key in ASSERTIONS:
        raise ValueError(f"assertion {key!r} already registered")
    ASSERTIONS[key] = cls
    return cls


def _ensure_assert(result: AssertionResult) -> None:
    if not result.passed:
        raise McpTestAssertionError(result)


class _AssertionBase:
    """Mixin providing the `.assert_()` method shared by every assertion."""

    yaml_key: ClassVar[str] = ""

    def check(self, trace: Trace) -> AssertionResult:  # pragma: no cover
        raise NotImplementedError

    def assert_(self, trace: Trace) -> None:
        _ensure_assert(self.check(trace))


def check_all(
    assertions: list[TraceAssertion], trace: Trace
) -> list[AssertionResult]:
    return [a.check(trace) for a in assertions]


def assert_all(assertions: list[TraceAssertion], trace: Trace) -> None:
    """Evaluate every assertion and raise on the first failure."""
    for a in assertions:
        result = a.check(trace)
        _ensure_assert(result)


def parse_assertion(entry: dict[str, Any]) -> TraceAssertion:
    """Turn one YAML assertion entry into an assertion instance.

    The YAML form is a single-key mapping whose key names the assertion and
    whose value is the argument (scalar, list, or mapping). For example:

    - `{"tool_called": "create_issue"}` → `tool_called("create_issue")`
    - `{"max_tool_calls": 3}` → `max_tool_calls(3)`
    - `{"param_matches": {"tool": "x", "param": "y", "contains": "z"}}`
    """
    if not isinstance(entry, dict) or len(entry) != 1:
        raise ValueError(
            f"assertion entry must be a single-key mapping, got {entry!r}"
        )
    ((key, value),) = entry.items()
    if key not in ASSERTIONS:
        raise ValueError(
            f"unknown assertion {key!r}; known: {sorted(ASSERTIONS)}"
        )
    cls = ASSERTIONS[key]
    if isinstance(value, dict):
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError(f"bad arguments for assertion {key!r}: {exc}") from exc
    if isinstance(value, list):
        return cls(value)
    return cls(value)


def parse_assertions(entries: list[dict[str, Any]]) -> list[TraceAssertion]:
    return [parse_assertion(e) for e in entries]
