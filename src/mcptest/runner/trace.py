"""Trace dataclass — the canonical record of one agent run.

A `Trace` captures everything assertions need to evaluate: every mocked tool
call with its arguments, result, error state, latency; the agent's final
textual output; its exit code; and total duration. Traces serialize to JSON
so they can be snapshotted (Session 9) or shipped to the cloud backend
(Session 12).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcptest.mock_server.recorder import RecordedCall


def _new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Trace:
    """One completed agent run."""

    trace_id: str = field(default_factory=_new_trace_id)
    timestamp: str = field(default_factory=_utc_iso_now)
    input: str = ""
    output: str = ""
    tool_calls: list[RecordedCall] = field(default_factory=list)
    duration_s: float = 0.0
    exit_code: int = 0
    stderr: str = ""
    agent_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tool_calls(self) -> int:
        return len(self.tool_calls)

    @property
    def tool_names(self) -> list[str]:
        """Ordered list of tool names that were called (with duplicates)."""
        return [c.tool for c in self.tool_calls]

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.agent_error is None

    def calls_to(self, tool_name: str) -> list[RecordedCall]:
        return [c for c in self.tool_calls if c.tool == tool_name]

    def call_count(self, tool_name: str) -> int:
        return len(self.calls_to(tool_name))

    def errors(self) -> list[RecordedCall]:
        return [c for c in self.tool_calls if c.is_error]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "input": self.input,
            "output": self.output,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "duration_s": self.duration_s,
            "exit_code": self.exit_code,
            "stderr": self.stderr,
            "agent_error": self.agent_error,
            "metadata": self.metadata,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trace:
        return cls(
            trace_id=data.get("trace_id", _new_trace_id()),
            timestamp=data.get("timestamp", _utc_iso_now()),
            input=data.get("input", ""),
            output=data.get("output", ""),
            tool_calls=[RecordedCall.from_dict(c) for c in data.get("tool_calls", [])],
            duration_s=data.get("duration_s", 0.0),
            exit_code=data.get("exit_code", 0),
            stderr=data.get("stderr", ""),
            agent_error=data.get("agent_error"),
            metadata=data.get("metadata", {}),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Trace:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass(frozen=True)
class RetryResult:
    """Aggregated result across multiple retry attempts of a single test case.

    When ``retry == 1`` (the default), this wraps exactly one Trace and the
    semantics are identical to a plain pass/fail: ``pass_rate`` is 1.0 or 0.0
    and ``stability`` is 1.0.

    Fields
    ------
    traces:
        One Trace per attempt, in execution order.
    attempt_results:
        Per-attempt pass/fail booleans (same length as *traces*).
    tolerance:
        Fraction of attempts that must pass for the overall result to be
        considered passing (e.g. 0.8 means ≥80% must pass).
    passed:
        True when ``pass_rate >= tolerance``.
    pass_rate:
        Fraction of attempts that passed.
    stability:
        Consistency metric in [0.0, 1.0].  1.0 means all attempts produced the
        same pass/fail outcome (all-pass or all-fail).  Lower values indicate
        flakiness.  When there is only one attempt, stability is always 1.0.
    """

    traces: tuple[Trace, ...]
    attempt_results: tuple[bool, ...]
    tolerance: float
    passed: bool
    pass_rate: float
    stability: float

    @classmethod
    def from_attempts(
        cls,
        traces: list[Trace],
        attempt_results: list[bool],
        tolerance: float,
    ) -> RetryResult:
        """Construct a RetryResult from parallel lists of traces and outcomes."""
        n = len(traces)
        if n == 0:
            raise ValueError("RetryResult requires at least one attempt")
        pass_count = sum(1 for r in attempt_results if r)
        pass_rate = pass_count / n
        passed = pass_rate >= tolerance
        # Stability: 1.0 when all outcomes are identical, lower when mixed.
        if n == 1:
            stability = 1.0
        else:
            # Fraction of pairs that agree (i.e. same outcome).
            pairs = n * (n - 1) / 2
            agreements = sum(
                1
                for i in range(n)
                for j in range(i + 1, n)
                if attempt_results[i] == attempt_results[j]
            )
            stability = agreements / pairs
        return cls(
            traces=tuple(traces),
            attempt_results=tuple(attempt_results),
            tolerance=tolerance,
            passed=passed,
            pass_rate=pass_rate,
            stability=stability,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "traces": [t.to_dict() for t in self.traces],
            "attempt_results": list(self.attempt_results),
            "tolerance": self.tolerance,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "stability": self.stability,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetryResult:
        traces = [Trace.from_dict(t) for t in data.get("traces", [])]
        attempt_results = [bool(r) for r in data.get("attempt_results", [])]
        tolerance = float(data.get("tolerance", 1.0))
        return cls.from_attempts(traces, attempt_results, tolerance)
