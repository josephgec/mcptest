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
    from mcptest.runner.trace import RetryResult, Trace


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


# ---------------------------------------------------------------------------
# Metric 7: stability
# ---------------------------------------------------------------------------


@register_metric
class stability(_MetricBase):  # noqa: N801
    """Consistency of pass/fail outcomes across retry attempts.

    Score = 1.0 when all attempts produced the same outcome (perfectly stable
    or perfectly failing).  Lower scores indicate flakiness.  When no retry
    data is attached to the trace (single-attempt runs), the score is 1.0
    and the details note explains why.

    This metric reads ``trace.metadata["retry_result"]`` — a dict serialized
    from ``RetryResult.to_dict()`` — if present.  The CLI populates this
    automatically when ``retry > 1``.
    """

    name: ClassVar[str] = "stability"
    label: ClassVar[str] = "Stability"

    def compute(
        self,
        trace: Trace,
        *,
        reference: Trace | None = None,
        fixtures: list[Fixture] | None = None,
    ) -> MetricResult:
        retry_data = trace.metadata.get("retry_result")
        if retry_data is None:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"note": "single-attempt run — no retry data"},
            )

        attempt_results: list[bool] = [bool(r) for r in retry_data.get("attempt_results", [])]
        n = len(attempt_results)
        if n == 0:
            return _result(
                name=self.name,
                score=1.0,
                label=self.label,
                details={"note": "no attempt results in retry data"},
            )

        pass_count = sum(1 for r in attempt_results if r)
        fail_count = n - pass_count
        pass_rate = pass_count / n

        # Stability: fraction of same-outcome pairs.
        if n == 1:
            stability_score = 1.0
        else:
            pairs = n * (n - 1) / 2
            agreements = sum(
                1
                for i in range(n)
                for j in range(i + 1, n)
                if attempt_results[i] == attempt_results[j]
            )
            stability_score = agreements / pairs

        # Trajectory variance: average pairwise Levenshtein distance across
        # attempts, normalized by max trajectory length seen.
        traces_data = retry_data.get("traces", [])
        trajectory_variance = _trajectory_variance(traces_data)

        return _result(
            name=self.name,
            score=stability_score,
            label=self.label,
            details={
                "attempts": n,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "pass_rate": round(pass_rate, 4),
                "trajectory_variance": round(trajectory_variance, 4),
            },
        )


def _trajectory_variance(traces_data: list[dict[str, Any]]) -> float:
    """Mean pairwise normalized Levenshtein distance across attempt tool sequences."""
    if len(traces_data) <= 1:
        return 0.0

    tool_seqs: list[list[str]] = [
        [c.get("tool", "") for c in t.get("tool_calls", [])]
        for t in traces_data
    ]

    distances: list[float] = []
    for i in range(len(tool_seqs)):
        for j in range(i + 1, len(tool_seqs)):
            a, b = tool_seqs[i], tool_seqs[j]
            if not a and not b:
                distances.append(0.0)
                continue
            max_len = max(len(a), len(b))
            dist = _levenshtein(a, b)
            distances.append(dist / max_len)

    return sum(distances) / len(distances) if distances else 0.0
