"""Concrete metric implementations.

Each class is a plain class with a `name` and `label` class attribute,
registered via `@register_metric`, and a `compute(trace, *, reference,
fixtures) -> MetricResult` method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]

from mcptest.metrics.base import MetricResult, _MetricBase, register_metric

if TYPE_CHECKING:
    from mcptest.fixtures.models import Fixture
    from mcptest.runner.trace import Trace


def _result(*, name: str, score: float, label: str, details: dict[str, Any] | None = None) -> MetricResult:
    return MetricResult(name=name, score=score, label=label, details=details or {})


# ---------------------------------------------------------------------------
# Metric 1: tool_efficiency
# ---------------------------------------------------------------------------


@register_metric
class tool_efficiency(_MetricBase):  # noqa: N801
    """Ratio of unique tools to total calls. Penalises repetitive tool use."""

    name: ClassVar[str] = "tool_efficiency"
    label: ClassVar[str] = "Tool Efficiency"

    def compute(
        self,
        trace: Trace,
        *,
        reference: Trace | None = None,
        fixtures: list[Fixture] | None = None,
    ) -> MetricResult:
        names = trace.tool_names
        total = len(names)
        if total == 0:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"unique": 0, "total": 0, "repeated": []},
            )
        unique_names = set(names)
        repeated = [n for n in unique_names if names.count(n) > 1]
        score = len(unique_names) / total
        return _result(
            name=self.name,
            score=score,
            label=self.label,
            details={"unique": len(unique_names), "total": total, "repeated": sorted(repeated)},
        )


# ---------------------------------------------------------------------------
# Metric 2: redundancy
# ---------------------------------------------------------------------------


@register_metric
class redundancy(_MetricBase):  # noqa: N801
    """Fraction of calls that are NOT exact duplicates (same tool + same args)."""

    name: ClassVar[str] = "redundancy"
    label: ClassVar[str] = "Non-Redundancy"

    def compute(
        self,
        trace: Trace,
        *,
        reference: Trace | None = None,
        fixtures: list[Fixture] | None = None,
    ) -> MetricResult:
        calls = trace.tool_calls
        total = len(calls)
        if total <= 1:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"duplicate_count": 0, "duplicated_calls": []},
            )

        seen: list[tuple[str, str]] = []
        duplicate_count = 0
        duplicated_calls: list[str] = []
        for call in calls:
            # Represent arguments as a sorted JSON-like string for hashability.
            key = (call.tool, _args_key(call.arguments))
            if key in seen:
                duplicate_count += 1
                duplicated_calls.append(call.tool)
            else:
                seen.append(key)

        score = 1.0 - (duplicate_count / total)
        return _result(
            name=self.name,
            score=score,
            label=self.label,
            details={"duplicate_count": duplicate_count, "duplicated_calls": duplicated_calls},
        )


def _args_key(arguments: dict[str, Any]) -> str:
    """Stable string representation of a call's arguments dict."""
    import json
    return json.dumps(arguments, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Metric 3: error_recovery_rate
# ---------------------------------------------------------------------------


@register_metric
class error_recovery_rate(_MetricBase):  # noqa: N801
    """For each error call, was there at least one successful call after it?"""

    name: ClassVar[str] = "error_recovery_rate"
    label: ClassVar[str] = "Error Recovery Rate"

    def compute(
        self,
        trace: Trace,
        *,
        reference: Trace | None = None,
        fixtures: list[Fixture] | None = None,
    ) -> MetricResult:
        calls = trace.tool_calls
        error_indices = [i for i, c in enumerate(calls) if c.is_error]
        error_count = len(error_indices)

        if error_count == 0:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"error_count": 0, "recovered": 0, "unrecovered_indices": []},
            )

        recovered = 0
        unrecovered_indices: list[int] = []
        for idx in error_indices:
            # Check if there is at least one successful call after this error.
            has_success_after = any(
                not calls[j].is_error for j in range(idx + 1, len(calls))
            )
            if has_success_after:
                recovered += 1
            else:
                unrecovered_indices.append(idx)

        score = recovered / error_count
        return _result(
            name=self.name,
            score=score,
            label=self.label,
            details={
                "error_count": error_count,
                "recovered": recovered,
                "unrecovered_indices": unrecovered_indices,
            },
        )


# ---------------------------------------------------------------------------
# Metric 4: trajectory_similarity
# ---------------------------------------------------------------------------


@register_metric
class trajectory_similarity(_MetricBase):  # noqa: N801
    """Normalized Levenshtein similarity between current and reference tool sequences."""

    name: ClassVar[str] = "trajectory_similarity"
    label: ClassVar[str] = "Trajectory Similarity"

    def compute(
        self,
        trace: Trace,
        *,
        reference: Trace | None = None,
        fixtures: list[Fixture] | None = None,
    ) -> MetricResult:
        if reference is None:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"note": "no reference trace provided", "current_tools": trace.tool_names},
            )

        a = trace.tool_names
        b = reference.tool_names

        if not a and not b:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"distance": 0, "max_length": 0, "current_tools": [], "reference_tools": []},
            )

        max_len = max(len(a), len(b))
        distance = _levenshtein(a, b)
        score = 1.0 - (distance / max_len)

        return _result(
            name=self.name,
            score=score,
            label=self.label,
            details={
                "distance": distance,
                "max_length": max_len,
                "current_tools": a,
                "reference_tools": b,
            },
        )


def _levenshtein(a: list[str], b: list[str]) -> int:
    """Pure-Python Levenshtein distance on two lists of strings."""
    la, lb = len(a), len(b)
    # dp[i][j] = edit distance between a[:i] and b[:j]
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, lb + 1):
            if a[i - 1] == b[j - 1]:
                dp[j] = prev[j - 1]
            else:
                dp[j] = 1 + min(prev[j], dp[j - 1], prev[j - 1])
    return dp[lb]


# ---------------------------------------------------------------------------
# Metric 5: schema_compliance
# ---------------------------------------------------------------------------


@register_metric
class schema_compliance(_MetricBase):  # noqa: N801
    """Fraction of tool calls whose arguments pass the tool's declared input_schema."""

    name: ClassVar[str] = "schema_compliance"
    label: ClassVar[str] = "Schema Compliance"

    def compute(
        self,
        trace: Trace,
        *,
        reference: Trace | None = None,
        fixtures: list[Fixture] | None = None,
    ) -> MetricResult:
        if not fixtures:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"note": "no fixtures provided"},
            )

        if jsonschema is None:  # pragma: no cover
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"note": "jsonschema package not available"},
            )

        calls = trace.tool_calls
        total = len(calls)
        if total == 0:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"valid": 0, "invalid": 0, "violations": []},
            )

        # Build a tool-name → schema lookup from all fixtures.
        schema_map: dict[str, dict[str, Any]] = {}
        for fixture in fixtures:
            for tool_spec in fixture.tools:
                schema_map[tool_spec.name] = tool_spec.input_schema

        valid = 0
        violations: list[dict[str, Any]] = []
        for i, call in enumerate(calls):
            schema = schema_map.get(call.tool)
            if schema is None:
                # Tool not declared in any fixture — treat as compliant.
                valid += 1
                continue
            try:
                jsonschema.validate(instance=call.arguments, schema=schema)
                valid += 1
            except jsonschema.ValidationError as exc:
                violations.append({"tool": call.tool, "index": i, "error": exc.message})

        invalid = total - valid
        score = valid / total
        return _result(
            name=self.name,
            score=score,
            label=self.label,
            details={"valid": valid, "invalid": invalid, "violations": violations},
        )


# ---------------------------------------------------------------------------
# Metric 6: tool_coverage
# ---------------------------------------------------------------------------


@register_metric
class tool_coverage(_MetricBase):  # noqa: N801
    """Fraction of fixture-declared tools that the agent actually used."""

    name: ClassVar[str] = "tool_coverage"
    label: ClassVar[str] = "Tool Coverage"

    def compute(
        self,
        trace: Trace,
        *,
        reference: Trace | None = None,
        fixtures: list[Fixture] | None = None,
    ) -> MetricResult:
        if not fixtures:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"note": "no fixtures provided"},
            )

        available: list[str] = []
        for fixture in fixtures:
            for tool_spec in fixture.tools:
                if tool_spec.name not in available:
                    available.append(tool_spec.name)

        if not available:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"used": [], "available": [], "unused": []},
            )

        used_set = set(trace.tool_names)
        used = [t for t in available if t in used_set]
        unused = [t for t in available if t not in used_set]
        score = len(used) / len(available)
        return _result(
            name=self.name,
            score=score,
            label=self.label,
            details={"used": used, "available": available, "unused": unused},
        )
