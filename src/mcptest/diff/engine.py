"""Diff engine that compares a current Trace against a baseline Trace.

Produces a `TraceDiff` with zero or more `Regression` entries. Each regression
is categorized so a reporter can group them and so CI gates can decide which
categories should fail the build.

Categories (the string values are what gets serialized to JSON output):

- `tool_selection`  — the ordered list of tool names differs.
- `tool_count`      — same tools but different invocation counts.
- `parameter_drift` — same tool at the same position, different arguments.
- `result_drift`    — same tool called the same way but returned a different
                      result payload.
- `latency`         — total duration increased beyond a configurable percentage.
- `output`          — the agent's final text output differs.
- `error`           — a tool call errored in one trace but not the other.

The engine does not decide which of these are fatal — that's up to the caller
(CLI gate, snapshot command, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from mcptest.runner.trace import Trace


class RegressionKind:
    TOOL_SELECTION: ClassVar[str] = "tool_selection"
    TOOL_COUNT: ClassVar[str] = "tool_count"
    PARAMETER_DRIFT: ClassVar[str] = "parameter_drift"
    RESULT_DRIFT: ClassVar[str] = "result_drift"
    LATENCY: ClassVar[str] = "latency"
    OUTPUT: ClassVar[str] = "output"
    ERROR: ClassVar[str] = "error"


@dataclass
class Regression:
    """One detected difference between baseline and current trace."""

    kind: str
    message: str
    old: Any = None
    new: Any = None
    call_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "old": self.old,
            "new": self.new,
            "call_index": self.call_index,
        }


@dataclass
class TraceDiff:
    """The aggregate result of comparing two traces."""

    regressions: list[Regression] = field(default_factory=list)

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)

    def by_kind(self, kind: str) -> list[Regression]:
        return [r for r in self.regressions if r.kind == kind]

    def to_dict(self) -> dict[str, Any]:
        return {
            "regressions": [r.to_dict() for r in self.regressions],
            "count": len(self.regressions),
            "kinds": sorted({r.kind for r in self.regressions}),
        }


def diff_traces(
    baseline: Trace,
    current: Trace,
    *,
    latency_threshold_pct: float = 50.0,
    fuzzy_output: bool = True,
) -> TraceDiff:
    """Compare two traces and return the set of detected regressions.

    Args:
        baseline: the trusted reference trace.
        current: the freshly observed trace to check.
        latency_threshold_pct: report a latency regression only when the
            current run is at least this percentage slower than the
            baseline. Defaults to 50% to stay off typical scheduler noise.
        fuzzy_output: if True, strip surrounding whitespace before
            comparing the final agent output. Set False for byte-exact
            matching (useful when tests assert on trailing newlines).
    """
    regressions: list[Regression] = []

    base_names = baseline.tool_names
    curr_names = current.tool_names

    if base_names != curr_names:
        regressions.append(
            Regression(
                kind=RegressionKind.TOOL_SELECTION,
                message=(
                    f"tool sequence changed: {base_names!r} → {curr_names!r}"
                ),
                old=base_names,
                new=curr_names,
            )
        )

        base_counts = _counts(base_names)
        curr_counts = _counts(curr_names)
        for tool in sorted(set(base_counts) | set(curr_counts)):
            if base_counts.get(tool, 0) != curr_counts.get(tool, 0):
                regressions.append(
                    Regression(
                        kind=RegressionKind.TOOL_COUNT,
                        message=(
                            f"{tool}: {base_counts.get(tool, 0)} call(s) → "
                            f"{curr_counts.get(tool, 0)} call(s)"
                        ),
                        old=base_counts.get(tool, 0),
                        new=curr_counts.get(tool, 0),
                    )
                )
    else:
        # Same trajectory — compare each call position for drift.
        for i, (a, b) in enumerate(zip(baseline.tool_calls, current.tool_calls)):
            if a.arguments != b.arguments:
                regressions.append(
                    Regression(
                        kind=RegressionKind.PARAMETER_DRIFT,
                        message=f"call #{i} ({a.tool}) arguments drifted",
                        old=a.arguments,
                        new=b.arguments,
                        call_index=i,
                    )
                )
            if a.result != b.result:
                regressions.append(
                    Regression(
                        kind=RegressionKind.RESULT_DRIFT,
                        message=f"call #{i} ({a.tool}) result differs",
                        old=a.result,
                        new=b.result,
                        call_index=i,
                    )
                )
            if a.is_error != b.is_error:
                regressions.append(
                    Regression(
                        kind=RegressionKind.ERROR,
                        message=(
                            f"call #{i} ({a.tool}) error state flipped: "
                            f"{a.error!r} → {b.error!r}"
                        ),
                        old=a.error,
                        new=b.error,
                        call_index=i,
                    )
                )

    if baseline.duration_s > 0:
        pct = ((current.duration_s - baseline.duration_s) / baseline.duration_s) * 100
        if pct > latency_threshold_pct:
            regressions.append(
                Regression(
                    kind=RegressionKind.LATENCY,
                    message=(
                        f"duration increased by {pct:.1f}% "
                        f"({baseline.duration_s:.3f}s → {current.duration_s:.3f}s)"
                    ),
                    old=baseline.duration_s,
                    new=current.duration_s,
                )
            )

    base_out = baseline.output.strip() if fuzzy_output else baseline.output
    curr_out = current.output.strip() if fuzzy_output else current.output
    if base_out != curr_out:
        regressions.append(
            Regression(
                kind=RegressionKind.OUTPUT,
                message="final output differs",
                old=baseline.output,
                new=current.output,
            )
        )

    return TraceDiff(regressions=regressions)


def _counts(names: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for n in names:
        out[n] = out.get(n, 0) + 1
    return out
