"""Assertion combinators: all_of, any_of, none_of, weighted_score.

These assertions operate on other assertions (or metrics) rather than directly
on trace properties, enabling complex boolean logic and quality-score gating
in YAML test files.

YAML examples::

    - all_of:
        - tool_called: create_issue
        - max_tool_calls: 5

    - any_of:
        - tool_called: create_issue
        - output_contains: created

    - none_of:
        - tool_called: delete_all
        - output_contains: ERROR

    - weighted_score:
        threshold: 0.75
        weights:
          tool_efficiency: 0.3
          redundancy: 0.2
          error_recovery_rate: 0.5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from mcptest.assertions.base import (
    AssertionResult,
    TraceAssertion,
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
# Boolean combinators
# ---------------------------------------------------------------------------


@register_assertion
@dataclass
class all_of(_AssertionBase):  # noqa: N801
    """Pass iff every sub-assertion passes.

    Accepts a list of assertion dicts (same format as the YAML ``assertions:``
    key).  All must pass.

    YAML::

        - all_of:
            - tool_called: create_issue
            - max_tool_calls: 5
    """

    assertions: list[dict[str, Any]]
    yaml_key: ClassVar[str] = "all_of"
    _parsed: list[TraceAssertion] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        from mcptest.assertions.base import parse_assertions

        self._parsed = parse_assertions(self.assertions)

    def check(self, trace: Trace) -> AssertionResult:
        failed = [r for a in self._parsed if not (r := a.check(trace)).passed]
        if not failed:
            return _result(
                passed=True,
                name=self.yaml_key,
                message=f"all {len(self._parsed)} sub-assertion(s) passed",
                details={"count": len(self._parsed)},
            )
        first = failed[0]
        return _result(
            passed=False,
            name=self.yaml_key,
            message=f"all_of failed ({len(failed)}/{len(self._parsed)} failed): {first.message}",
            details={
                "failed_count": len(failed),
                "total": len(self._parsed),
                "first_failure": first.to_dict(),
            },
        )


@register_assertion
@dataclass
class any_of(_AssertionBase):  # noqa: N801
    """Pass iff at least one sub-assertion passes.

    YAML::

        - any_of:
            - tool_called: create_issue
            - output_contains: created
    """

    assertions: list[dict[str, Any]]
    yaml_key: ClassVar[str] = "any_of"
    _parsed: list[TraceAssertion] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        from mcptest.assertions.base import parse_assertions

        self._parsed = parse_assertions(self.assertions)

    def check(self, trace: Trace) -> AssertionResult:
        results = [a.check(trace) for a in self._parsed]
        passed_results = [r for r in results if r.passed]
        if passed_results:
            return _result(
                passed=True,
                name=self.yaml_key,
                message=f"any_of passed: {passed_results[0].message}",
                details={"passed_count": len(passed_results), "total": len(self._parsed)},
            )
        return _result(
            passed=False,
            name=self.yaml_key,
            message=f"any_of: none of {len(self._parsed)} sub-assertion(s) passed",
            details={
                "total": len(self._parsed),
                "failures": [r.to_dict() for r in results],
            },
        )


@register_assertion
@dataclass
class none_of(_AssertionBase):  # noqa: N801
    """Pass iff no sub-assertion passes.

    YAML::

        - none_of:
            - tool_called: delete_all
            - output_contains: ERROR
    """

    assertions: list[dict[str, Any]]
    yaml_key: ClassVar[str] = "none_of"
    _parsed: list[TraceAssertion] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        from mcptest.assertions.base import parse_assertions

        self._parsed = parse_assertions(self.assertions)

    def check(self, trace: Trace) -> AssertionResult:
        passed_results = [r for a in self._parsed if (r := a.check(trace)).passed]
        if not passed_results:
            return _result(
                passed=True,
                name=self.yaml_key,
                message=f"none_of passed: none of {len(self._parsed)} sub-assertion(s) triggered",
                details={"total": len(self._parsed)},
            )
        first = passed_results[0]
        return _result(
            passed=False,
            name=self.yaml_key,
            message=f"none_of failed: {len(passed_results)} sub-assertion(s) unexpectedly passed: {first.message}",
            details={
                "unexpected_passes": len(passed_results),
                "total": len(self._parsed),
                "first_pass": first.to_dict(),
            },
        )


# ---------------------------------------------------------------------------
# Weighted composite score assertion
# ---------------------------------------------------------------------------


@register_assertion
@dataclass
class weighted_score(_AssertionBase):  # noqa: N801
    """Pass iff a weighted average of named metrics meets threshold.

    Each metric is computed, multiplied by its weight, summed, then divided by
    the total weight.  The result must be >= ``threshold`` to pass.

    YAML::

        - weighted_score:
            threshold: 0.75
            weights:
              tool_efficiency: 0.3
              redundancy: 0.2
              error_recovery_rate: 0.5
    """

    threshold: float
    weights: dict[str, float]
    yaml_key: ClassVar[str] = "weighted_score"

    def check(self, trace: Trace) -> AssertionResult:
        from mcptest.metrics.base import METRICS

        if not self.weights:
            return _result(
                passed=False,
                name=self.yaml_key,
                message="weighted_score requires at least one metric weight",
            )

        unknown = [m for m in self.weights if m not in METRICS]
        if unknown:
            return _result(
                passed=False,
                name=self.yaml_key,
                message=f"unknown metric(s): {unknown}; known: {sorted(METRICS)}",
                details={"unknown": unknown},
            )

        scores: dict[str, float] = {}
        for metric_name in self.weights:
            instance = METRICS[metric_name]()
            scores[metric_name] = instance.compute(trace).score

        total_weight = sum(self.weights.values())
        if total_weight == 0:
            return _result(
                passed=False,
                name=self.yaml_key,
                message="weighted_score: total weight is zero",
            )

        composite = sum(scores[m] * w for m, w in self.weights.items()) / total_weight
        passed = composite >= self.threshold
        return _result(
            passed=passed,
            name=self.yaml_key,
            message=(
                f"weighted score {composite:.3f} >= {self.threshold:.3f}"
                if passed
                else f"weighted score {composite:.3f} < threshold {self.threshold:.3f}"
            ),
            details={
                "composite": composite,
                "threshold": self.threshold,
                "scores": scores,
                "weights": self.weights,
            },
        )


# Satisfy the unused-import linter.
_ = field
