"""Tests for non-deterministic agent testing: retry, tolerance & stability.

Session 21: RetryResult, run_with_retry, --retry/--tolerance CLI flags,
stability metric, and JUnit/TAP exporter retry extensions.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import yaml

from mcptest.assertions.base import AssertionResult
from mcptest.cli.commands import CaseResult
from mcptest.exporters import JUnitExporter, TAPExporter
from mcptest.metrics.base import MetricResult
from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner import CallableAdapter, RetryResult, Runner
from mcptest.runner.trace import Trace


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _trace(
    *,
    trace_id: str = "abc123",
    tool_calls: list[RecordedCall] | None = None,
    duration_s: float = 0.1,
    exit_code: int = 0,
    agent_error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Trace:
    return Trace(
        trace_id=trace_id,
        input="",
        output="ok",
        tool_calls=tool_calls or [],
        duration_s=duration_s,
        exit_code=exit_code,
        agent_error=agent_error,
        metadata=metadata or {},
    )


def _call(tool: str) -> RecordedCall:
    return RecordedCall(tool=tool, arguments={}, result={})


def _fixture_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "fix.yaml"
    p.write_text(
        "server: {name: mock-test}\n"
        "tools:\n"
        "  - name: ping\n"
        "    responses:\n"
        "      - return_text: pong\n"
    )
    return p


def _mk_runner(tmp_path: Path, agent_fn=None) -> Runner:
    fix = _fixture_yaml(tmp_path)
    if agent_fn is None:
        agent_fn = lambda inp, env: "ok"  # noqa: E731
    return Runner(fixtures=[str(fix)], agent=CallableAdapter(agent_fn))


def _passing_result(
    suite: str = "s",
    case: str = "c",
    retry_result: RetryResult | None = None,
) -> CaseResult:
    return CaseResult(
        suite_name=suite,
        case_name=case,
        trace=_trace(),
        assertion_results=[AssertionResult(passed=True, name="ok", message="ok")],
        retry_result=retry_result,
    )


def _failing_result(
    suite: str = "s",
    case: str = "c",
    retry_result: RetryResult | None = None,
) -> CaseResult:
    return CaseResult(
        suite_name=suite,
        case_name=case,
        trace=_trace(),
        assertion_results=[
            AssertionResult(passed=False, name="tool_called", message="not called")
        ],
        retry_result=retry_result,
    )


# ---------------------------------------------------------------------------
# 1. TestCase model — retry / tolerance fields
# ---------------------------------------------------------------------------


class TestTestCaseModel:
    def test_defaults(self) -> None:
        from mcptest.testspec.models import TestCase

        tc = TestCase(name="x")
        assert tc.retry == 1
        assert tc.tolerance == 1.0

    def test_explicit_values(self) -> None:
        from mcptest.testspec.models import TestCase

        tc = TestCase(name="x", retry=3, tolerance=0.67)
        assert tc.retry == 3
        assert pytest.approx(tc.tolerance) == 0.67

    def test_retry_minimum_one(self) -> None:
        from mcptest.testspec.models import TestCase

        with pytest.raises(Exception):
            TestCase(name="x", retry=0)

    def test_tolerance_bounds(self) -> None:
        from mcptest.testspec.models import TestCase

        with pytest.raises(Exception):
            TestCase(name="x", tolerance=1.1)
        with pytest.raises(Exception):
            TestCase(name="x", tolerance=-0.1)

    def test_tolerance_extremes_valid(self) -> None:
        from mcptest.testspec.models import TestCase

        assert TestCase(name="x", tolerance=0.0).tolerance == 0.0
        assert TestCase(name="x", tolerance=1.0).tolerance == 1.0

    def test_parsed_from_yaml(self, tmp_path: Path) -> None:
        from mcptest.testspec.models import TestCase

        raw = yaml.safe_load("name: my_case\nretry: 5\ntolerance: 0.8\n")
        tc = TestCase(**raw)
        assert tc.retry == 5
        assert pytest.approx(tc.tolerance) == 0.8

    def test_extra_fields_forbidden(self) -> None:
        from mcptest.testspec.models import TestCase

        with pytest.raises(Exception):
            TestCase(name="x", unknown_field="y")


# ---------------------------------------------------------------------------
# 2. RetryResult — construction, computation, serialization
# ---------------------------------------------------------------------------


class TestRetryResultConstruction:
    def test_single_pass(self) -> None:
        rr = RetryResult.from_attempts([_trace()], [True], 1.0)
        assert rr.passed is True
        assert rr.pass_rate == 1.0
        assert rr.stability == 1.0
        assert len(rr.traces) == 1

    def test_single_fail(self) -> None:
        rr = RetryResult.from_attempts([_trace()], [False], 1.0)
        assert rr.passed is False
        assert rr.pass_rate == 0.0
        assert rr.stability == 1.0

    def test_all_pass_three(self) -> None:
        traces = [_trace() for _ in range(3)]
        rr = RetryResult.from_attempts(traces, [True, True, True], 1.0)
        assert rr.passed is True
        assert rr.pass_rate == 1.0
        assert rr.stability == 1.0

    def test_all_fail_three(self) -> None:
        traces = [_trace() for _ in range(3)]
        rr = RetryResult.from_attempts(traces, [False, False, False], 1.0)
        assert rr.passed is False
        assert rr.pass_rate == 0.0
        assert rr.stability == 1.0

    def test_mixed_two_out_of_three(self) -> None:
        traces = [_trace() for _ in range(3)]
        # 2/3 = 0.6667, tolerance=0.60 → passes
        rr = RetryResult.from_attempts(traces, [True, False, True], 0.60)
        assert rr.passed is True
        assert pytest.approx(rr.pass_rate, abs=1e-4) == 2 / 3
        # 3 pairs: (T,F)=disagree, (T,T)=agree, (F,T)=disagree → 1/3
        assert pytest.approx(rr.stability, abs=1e-4) == 1 / 3

    def test_exactly_meeting_tolerance(self) -> None:
        traces = [_trace() for _ in range(5)]
        # 4/5 = 0.8, tolerance = 0.8 → should pass
        rr = RetryResult.from_attempts(traces, [True, True, True, True, False], 0.8)
        assert rr.passed is True

    def test_just_below_tolerance(self) -> None:
        traces = [_trace() for _ in range(5)]
        # 3/5 = 0.6 < tolerance 0.8 → fail
        rr = RetryResult.from_attempts(
            traces, [True, True, True, False, False], 0.8
        )
        assert rr.passed is False

    def test_tolerance_zero_always_passes(self) -> None:
        rr = RetryResult.from_attempts([_trace()], [False], 0.0)
        assert rr.passed is True

    def test_empty_traces_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one attempt"):
            RetryResult.from_attempts([], [], 1.0)

    def test_immutable_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        rr = RetryResult.from_attempts([_trace()], [True], 1.0)
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            rr.passed = False  # type: ignore[misc]


class TestRetryResultStability:
    def test_two_same_outcomes_stable(self) -> None:
        rr = RetryResult.from_attempts([_trace(), _trace()], [True, True], 1.0)
        assert rr.stability == 1.0

    def test_two_different_outcomes_unstable(self) -> None:
        rr = RetryResult.from_attempts([_trace(), _trace()], [True, False], 1.0)
        assert rr.stability == 0.0

    def test_four_attempts_mixed(self) -> None:
        # [T, T, F, F] — pairs: TT, TF, TF, TF, FF, TF → 2 agree of 6
        traces = [_trace() for _ in range(4)]
        rr = RetryResult.from_attempts(traces, [True, True, False, False], 1.0)
        # Pairs: (0,1)=TT agree, (0,2)=TF disagree, (0,3)=TF disagree,
        #        (1,2)=TF disagree, (1,3)=TF disagree, (2,3)=FF agree → 2/6
        assert pytest.approx(rr.stability, abs=1e-4) == 2 / 6

    def test_all_same_fail_is_stable(self) -> None:
        traces = [_trace() for _ in range(4)]
        rr = RetryResult.from_attempts(traces, [False, False, False, False], 1.0)
        assert rr.stability == 1.0


class TestRetryResultSerialization:
    def test_round_trip(self) -> None:
        t1 = _trace(trace_id="t1", duration_s=0.1)
        t2 = _trace(trace_id="t2", duration_s=0.2)
        rr = RetryResult.from_attempts([t1, t2], [True, False], 0.5)
        d = rr.to_dict()
        restored = RetryResult.from_dict(d)
        assert restored.passed == rr.passed
        assert pytest.approx(restored.pass_rate) == rr.pass_rate
        assert pytest.approx(restored.stability) == rr.stability
        assert restored.tolerance == rr.tolerance
        assert len(restored.traces) == 2

    def test_to_dict_keys(self) -> None:
        rr = RetryResult.from_attempts([_trace()], [True], 1.0)
        d = rr.to_dict()
        assert set(d.keys()) == {
            "traces", "attempt_results", "tolerance", "passed", "pass_rate", "stability"
        }

    def test_from_dict_preserves_pass_rate(self) -> None:
        rr = RetryResult.from_attempts(
            [_trace() for _ in range(3)], [True, False, True], 0.67
        )
        d = rr.to_dict()
        restored = RetryResult.from_dict(d)
        assert pytest.approx(restored.pass_rate, abs=1e-4) == 2 / 3


# ---------------------------------------------------------------------------
# 3. Runner.run_with_retry
# ---------------------------------------------------------------------------


class TestRunWithRetry:
    def test_single_attempt_default(self, tmp_path: Path) -> None:
        runner = _mk_runner(tmp_path)
        rr = runner.run_with_retry("hi", retry=1, tolerance=1.0)
        assert len(rr.traces) == 1
        assert rr.passed is True

    def test_three_attempts_all_succeed(self, tmp_path: Path) -> None:
        runner = _mk_runner(tmp_path)
        rr = runner.run_with_retry("hi", retry=3, tolerance=1.0)
        assert len(rr.traces) == 3
        assert all(rr.attempt_results)
        assert rr.passed is True

    def test_custom_evaluate_fn(self, tmp_path: Path) -> None:
        call_count = 0

        def agent(inp: str, env: dict[str, str]) -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        runner = _mk_runner(tmp_path, agent)
        rr = runner.run_with_retry(
            "hi",
            retry=4,
            tolerance=1.0,
            evaluate=lambda trace: trace.output == "ok",
        )
        assert call_count == 4
        assert all(rr.attempt_results)

    def test_evaluate_fn_false(self, tmp_path: Path) -> None:
        runner = _mk_runner(tmp_path)
        rr = runner.run_with_retry(
            "hi",
            retry=2,
            tolerance=0.0,
            evaluate=lambda trace: False,
        )
        assert not any(rr.attempt_results)
        assert rr.pass_rate == 0.0
        # tolerance=0 so it still passes overall
        assert rr.passed is True

    def test_tolerance_gate(self, tmp_path: Path) -> None:
        results_iter = iter([True, False, True])

        def agent(inp: str, env: dict[str, str]) -> str:
            return "ok"

        runner = _mk_runner(tmp_path, agent)
        rr = runner.run_with_retry(
            "hi",
            retry=3,
            tolerance=0.8,
            evaluate=lambda t: next(results_iter),
        )
        # 2/3 = 0.667 < 0.8
        assert rr.passed is False

    def test_retry_invalid_raises(self, tmp_path: Path) -> None:
        runner = _mk_runner(tmp_path)
        with pytest.raises(ValueError, match="retry must be >= 1"):
            runner.run_with_retry("hi", retry=0)

    def test_metadata_propagated(self, tmp_path: Path) -> None:
        runner = _mk_runner(tmp_path)
        rr = runner.run_with_retry("hi", retry=2, metadata={"key": "val"})
        for t in rr.traces:
            assert t.metadata.get("key") == "val"


# ---------------------------------------------------------------------------
# 4. CLI --retry and --tolerance flags
# ---------------------------------------------------------------------------


class TestCLIRetryFlags:
    def _make_suite(self, tmp_path: Path, retry: int = 1, tolerance: float = 1.0) -> Path:
        """Write a minimal self-contained YAML test suite to tmp_path."""
        fix_path = _fixture_yaml(tmp_path)
        suite_path = tmp_path / "test_retry.yaml"
        suite_path.write_text(
            f"name: retry_suite\n"
            f"fixtures:\n  - {fix_path}\n"
            f"agent:\n  command: python -c \"print('ok')\"\n"
            f"cases:\n"
            f"  - name: case1\n"
            f"    input: hello\n"
            f"    retry: {retry}\n"
            f"    tolerance: {tolerance}\n"
        )
        return suite_path

    def test_run_command_has_retry_option(self) -> None:
        from click.testing import CliRunner
        from mcptest.cli.main import main

        result = CliRunner().invoke(main, ["run", "--help"])
        assert "--retry" in result.output
        assert "--tolerance" in result.output

    def test_retry_override_in_yaml(self, tmp_path: Path) -> None:
        """YAML-level retry fields are parsed and respected."""
        from mcptest.testspec import load_test_suite

        suite_path = self._make_suite(tmp_path, retry=3, tolerance=0.67)
        suite = load_test_suite(suite_path)
        case = suite.cases[0]
        assert case.retry == 3
        assert pytest.approx(case.tolerance) == 0.67

    def test_case_result_no_retry_result_for_single(self, tmp_path: Path) -> None:
        """Single-attempt case has retry_result=None."""
        from mcptest.cli.commands import _run_case
        from mcptest.testspec.models import TestCase
        from mcptest.testspec import TestSuite

        runner = _mk_runner(tmp_path)
        suite = TestSuite(
            name="s",
            agent=__import__("mcptest.testspec.models", fromlist=["AgentSpec"]).AgentSpec(
                command="echo"
            ),
            cases=[TestCase(name="c1")],
        )
        case = TestCase(name="c1", retry=1)
        result = _run_case(runner, suite, case)
        assert result.retry_result is None

    def test_case_result_has_retry_result_for_multi(self, tmp_path: Path) -> None:
        """Multi-attempt case stores RetryResult."""
        from mcptest.cli.commands import _run_case
        from mcptest.testspec.models import AgentSpec, TestCase
        from mcptest.testspec import TestSuite

        runner = _mk_runner(tmp_path)
        suite = TestSuite(
            name="s",
            agent=AgentSpec(command="echo"),
            cases=[TestCase(name="c1")],
        )
        case = TestCase(name="c1", retry=3, tolerance=1.0)
        result = _run_case(runner, suite, case)
        assert result.retry_result is not None
        assert len(result.retry_result.traces) == 3

    def test_retry_override_from_cli(self, tmp_path: Path) -> None:
        """--retry flag overrides per-case retry."""
        from mcptest.cli.commands import _run_case
        from mcptest.testspec.models import AgentSpec, TestCase
        from mcptest.testspec import TestSuite

        runner = _mk_runner(tmp_path)
        suite = TestSuite(
            name="s",
            agent=AgentSpec(command="echo"),
            cases=[TestCase(name="c1")],
        )
        case = TestCase(name="c1", retry=1)
        # Override: 4 retries from CLI
        result = _run_case(runner, suite, case, retry_override=4)
        assert result.retry_result is not None
        assert len(result.retry_result.traces) == 4

    def test_tolerance_override_from_cli(self, tmp_path: Path) -> None:
        """--tolerance flag overrides per-case tolerance."""
        from mcptest.cli.commands import _run_case
        from mcptest.testspec.models import AgentSpec, TestCase
        from mcptest.testspec import TestSuite

        runner = _mk_runner(tmp_path)
        suite = TestSuite(
            name="s",
            agent=AgentSpec(command="echo"),
            cases=[TestCase(name="c1")],
        )
        case = TestCase(name="c1", retry=3, tolerance=1.0)
        result = _run_case(runner, suite, case, tolerance_override=0.0)
        assert result.retry_result is not None
        assert result.retry_result.tolerance == 0.0

    def test_retry_override_none_uses_case_value(self, tmp_path: Path) -> None:
        from mcptest.cli.commands import _run_case
        from mcptest.testspec.models import AgentSpec, TestCase
        from mcptest.testspec import TestSuite

        runner = _mk_runner(tmp_path)
        suite = TestSuite(
            name="s",
            agent=AgentSpec(command="echo"),
            cases=[TestCase(name="c1")],
        )
        case = TestCase(name="c1", retry=2)
        result = _run_case(runner, suite, case, retry_override=None)
        assert result.retry_result is not None
        assert len(result.retry_result.traces) == 2


# ---------------------------------------------------------------------------
# 5. CaseResult.passed with retry_result
# ---------------------------------------------------------------------------


class TestCaseResultPassed:
    def test_passed_no_retry(self) -> None:
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(exit_code=0),
            assertion_results=[AssertionResult(passed=True, name="ok", message="ok")],
        )
        assert r.passed is True

    def test_failed_no_retry(self) -> None:
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(exit_code=0),
            assertion_results=[
                AssertionResult(passed=False, name="fail", message="no")
            ],
        )
        assert r.passed is False

    def test_passed_with_retry_result_overall_pass(self) -> None:
        rr = RetryResult.from_attempts([_trace(), _trace()], [True, True], 1.0)
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[],
            retry_result=rr,
        )
        assert r.passed is True

    def test_failed_with_retry_result_overall_fail(self) -> None:
        rr = RetryResult.from_attempts([_trace(), _trace()], [True, False], 1.0)
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[],
            retry_result=rr,
        )
        assert r.passed is False

    def test_error_overrides_retry_result(self) -> None:
        rr = RetryResult.from_attempts([_trace(), _trace()], [True, True], 1.0)
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[],
            error="something blew up",
            retry_result=rr,
        )
        assert r.passed is False

    def test_to_dict_includes_retry(self) -> None:
        rr = RetryResult.from_attempts([_trace(), _trace()], [True, False], 0.5)
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[],
            retry_result=rr,
        )
        d = r.to_dict()
        assert "retry" in d
        assert len(d["retry"]["traces"]) == 2

    def test_to_dict_no_retry_key_when_none(self) -> None:
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[],
        )
        d = r.to_dict()
        assert "retry" not in d

    def test_from_dict_round_trip_with_retry(self) -> None:
        rr = RetryResult.from_attempts([_trace(), _trace()], [True, True], 1.0)
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[AssertionResult(passed=True, name="ok", message="ok")],
            retry_result=rr,
        )
        restored = CaseResult.from_dict(r.to_dict())
        assert restored.retry_result is not None
        assert restored.retry_result.passed is True

    def test_from_dict_no_retry_round_trip(self) -> None:
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[AssertionResult(passed=True, name="ok", message="ok")],
        )
        restored = CaseResult.from_dict(r.to_dict())
        assert restored.retry_result is None


# ---------------------------------------------------------------------------
# 6. Stability metric
# ---------------------------------------------------------------------------


class TestStabilityMetric:
    def _metric(self, metadata: dict[str, Any]) -> Any:
        from mcptest.metrics.impls import stability

        trace = _trace(metadata=metadata)
        return stability().compute(trace)

    def test_no_retry_data_returns_1(self) -> None:
        result = self._metric({})
        assert result.name == "stability"
        assert result.score == 1.0
        assert "single-attempt" in result.details.get("note", "")

    def test_all_pass_is_stable(self) -> None:
        rr = RetryResult.from_attempts(
            [_trace() for _ in range(3)], [True, True, True], 1.0
        )
        result = self._metric({"retry_result": rr.to_dict()})
        assert result.score == 1.0
        assert result.details["attempts"] == 3
        assert result.details["pass_count"] == 3
        assert result.details["fail_count"] == 0

    def test_all_fail_is_stable(self) -> None:
        rr = RetryResult.from_attempts(
            [_trace() for _ in range(3)], [False, False, False], 1.0
        )
        result = self._metric({"retry_result": rr.to_dict()})
        assert result.score == 1.0
        assert result.details["fail_count"] == 3

    def test_alternating_is_unstable(self) -> None:
        rr = RetryResult.from_attempts(
            [_trace() for _ in range(4)], [True, False, True, False], 1.0
        )
        result = self._metric({"retry_result": rr.to_dict()})
        # Pairs: 6 total, agreements: (T,T)=1, (F,F)=1 → 2/6 = 0.333
        assert result.score < 0.5

    def test_two_pass_one_fail_partial_stability(self) -> None:
        rr = RetryResult.from_attempts(
            [_trace() for _ in range(3)], [True, True, False], 1.0
        )
        result = self._metric({"retry_result": rr.to_dict()})
        # Pairs: (T,T)=agree, (T,F)=disagree, (T,F)=disagree → 1/3
        assert pytest.approx(result.score, abs=1e-4) == 1 / 3

    def test_single_attempt_in_retry_data(self) -> None:
        rr = RetryResult.from_attempts([_trace()], [True], 1.0)
        result = self._metric({"retry_result": rr.to_dict()})
        assert result.score == 1.0

    def test_pass_rate_in_details(self) -> None:
        rr = RetryResult.from_attempts(
            [_trace() for _ in range(4)], [True, True, False, True], 1.0
        )
        result = self._metric({"retry_result": rr.to_dict()})
        assert pytest.approx(result.details["pass_rate"], abs=1e-4) == 0.75

    def test_trajectory_variance_identical_sequences(self) -> None:
        # All traces have identical tool sequences → variance = 0
        calls = [_call("ping")]
        traces = [_trace(tool_calls=[_call("ping")]) for _ in range(3)]
        rr = RetryResult.from_attempts(traces, [True, True, True], 1.0)
        result = self._metric({"retry_result": rr.to_dict()})
        assert result.details["trajectory_variance"] == 0.0

    def test_trajectory_variance_different_sequences(self) -> None:
        t1 = _trace(tool_calls=[_call("ping"), _call("pong")])
        t2 = _trace(tool_calls=[_call("foo")])
        rr = RetryResult.from_attempts([t1, t2], [True, True], 1.0)
        result = self._metric({"retry_result": rr.to_dict()})
        assert result.details["trajectory_variance"] > 0.0

    def test_stability_registered_in_metrics(self) -> None:
        from mcptest.metrics.base import METRICS

        assert "stability" in METRICS

    def test_stability_label(self) -> None:
        from mcptest.metrics.impls import stability

        assert stability.label == "Stability"

    def test_empty_attempt_results_in_retry_data(self) -> None:
        result = self._metric({"retry_result": {"attempt_results": [], "traces": []}})
        assert result.score == 1.0


# ---------------------------------------------------------------------------
# 7. JUnit exporter with retry data
# ---------------------------------------------------------------------------


def _mk_retry_result(
    n: int = 3,
    outcomes: list[bool] | None = None,
    tolerance: float = 0.67,
) -> RetryResult:
    if outcomes is None:
        outcomes = [True] * n
    traces = [_trace(duration_s=0.1) for _ in range(n)]
    return RetryResult.from_attempts(traces, outcomes, tolerance)


class TestJUnitRetryExport:
    def test_single_attempt_no_retry_attributes(self) -> None:
        r = _passing_result()
        xml_str = JUnitExporter().export([r])
        root = ET.fromstring(xml_str.split("\n", 1)[-1] if "?xml" in xml_str else xml_str)
        # Find the testcase element
        tc = root.find(".//testcase")
        assert tc is not None
        assert "attempts" not in tc.attrib
        assert "pass_rate" not in tc.attrib

    def test_multi_attempt_adds_attributes(self) -> None:
        rr = _mk_retry_result(3, [True, True, True])
        r = _passing_result(retry_result=rr)
        xml_str = JUnitExporter().export([r])
        root = ET.fromstring(xml_str.split("\n", 1)[-1] if "?xml" in xml_str else xml_str)
        tc = root.find(".//testcase")
        assert tc is not None
        assert tc.attrib.get("attempts") == "3"
        assert "pass_rate" in tc.attrib
        assert "stability" in tc.attrib

    def test_all_pass_no_flaky_element(self) -> None:
        rr = _mk_retry_result(3, [True, True, True])
        r = _passing_result(retry_result=rr)
        xml_str = JUnitExporter().export([r])
        assert "flakyFailure" not in xml_str

    def test_flaky_failure_element_emitted(self) -> None:
        rr = _mk_retry_result(3, [True, False, True], tolerance=1.0)
        r = _failing_result(retry_result=rr)
        xml_str = JUnitExporter().export([r])
        assert "flakyFailure" in xml_str

    def test_flaky_failure_message_contains_pass_rate(self) -> None:
        rr = _mk_retry_result(3, [True, False, True], tolerance=1.0)
        r = _failing_result(retry_result=rr)
        xml_str = JUnitExporter().export([r])
        root = ET.fromstring(xml_str.split("\n", 1)[-1] if "?xml" in xml_str else xml_str)
        flaky = root.find(".//flakyFailure")
        assert flaky is not None
        assert "2/3" in flaky.attrib.get("message", "")

    def test_all_fail_multi_attempt_uses_normal_failure(self) -> None:
        rr = _mk_retry_result(3, [False, False, False], tolerance=0.5)
        r = _failing_result(retry_result=rr)
        xml_str = JUnitExporter().export([r])
        # All fail and tolerance not met → normal failure, not flakyFailure
        assert "flakyFailure" not in xml_str
        assert "failure" in xml_str

    def test_flaky_type_attribute(self) -> None:
        rr = _mk_retry_result(3, [True, False, True], tolerance=1.0)
        r = _failing_result(retry_result=rr)
        xml_str = JUnitExporter().export([r])
        root = ET.fromstring(xml_str.split("\n", 1)[-1] if "?xml" in xml_str else xml_str)
        flaky = root.find(".//flakyFailure")
        assert flaky is not None
        assert flaky.attrib.get("type") == "FlakyFailure"

    def test_metric_properties_still_present(self) -> None:
        rr = _mk_retry_result(2, [True, True])
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[],
            metrics=[MetricResult(name="tool_efficiency", score=0.9, label="Tool Efficiency")],
            retry_result=rr,
        )
        xml_str = JUnitExporter().export([r])
        assert "mcptest.metric.tool_efficiency" in xml_str


# ---------------------------------------------------------------------------
# 8. TAP exporter with retry data
# ---------------------------------------------------------------------------


class TestTAPRetryExport:
    def test_single_attempt_no_subtest_lines(self) -> None:
        r = _passing_result()
        tap = TAPExporter().export([r])
        # Should not have a nested plan (subtests)
        lines = tap.splitlines()
        ok_line = next(l for l in lines if l.startswith("ok "))
        assert "pass_rate" not in ok_line
        # No nested plan line (lines starting with spaces + number plan)
        assert not any(l.startswith("    1..") for l in lines)

    def test_multi_attempt_adds_pass_rate_to_ok_line(self) -> None:
        rr = _mk_retry_result(3, [True, True, True])
        r = _passing_result(retry_result=rr)
        tap = TAPExporter().export([r])
        ok_line = next(l for l in tap.splitlines() if l.startswith("ok "))
        assert "pass_rate" in ok_line
        assert "stability" in ok_line

    def test_multi_attempt_subtests_emitted(self) -> None:
        rr = _mk_retry_result(3, [True, False, True])
        r = _passing_result(retry_result=rr)
        tap = TAPExporter().export([r])
        lines = tap.splitlines()
        # Subtest plan
        assert any("1..3" in l for l in lines)
        # Individual attempt ok/not-ok lines (contain "- attempt N")
        attempt_lines = [l for l in lines if "- attempt " in l]
        assert len(attempt_lines) == 3

    def test_attempt_lines_ok_not_ok(self) -> None:
        rr = _mk_retry_result(3, [True, False, True])
        r = _passing_result(retry_result=rr)
        tap = TAPExporter().export([r])
        # Use "- attempt " to avoid matching YAML diagnostic "attempts:" keys.
        attempt_lines = [l for l in tap.splitlines() if "- attempt " in l]
        statuses = [l.strip().split()[0] for l in attempt_lines]
        assert statuses == ["ok", "not", "ok"]

    def test_failing_multi_attempt_diag_has_retry_block(self) -> None:
        rr = _mk_retry_result(3, [True, False, True], tolerance=1.0)
        r = _failing_result(retry_result=rr)
        tap = TAPExporter().export([r])
        assert "retry:" in tap

    def test_failing_multi_attempt_diag_message_is_flaky(self) -> None:
        rr = _mk_retry_result(3, [True, False, True], tolerance=1.0)
        r = _failing_result(retry_result=rr)
        tap = TAPExporter().export([r])
        assert "Flaky" in tap

    def test_passing_single_attempt_no_subtest(self) -> None:
        r = _passing_result()
        tap = TAPExporter().export([r])
        # The top-level plan "1..1" is valid; check there is no *nested* subtest plan.
        assert not any(l.startswith("    1..") for l in tap.splitlines())
        assert tap.startswith("TAP version 14")

    def test_tap_header_preserved_with_retry(self) -> None:
        rr = _mk_retry_result(2, [True, True])
        r = _passing_result(retry_result=rr)
        tap = TAPExporter().export([r])
        assert tap.startswith("TAP version 14")
        assert "1..1" in tap.splitlines()[1]

    def test_retry_block_in_diagnostic_has_expected_keys(self) -> None:
        rr = _mk_retry_result(3, [True, False, True], tolerance=0.67)
        r = _failing_result(retry_result=rr)
        tap = TAPExporter().export([r])
        assert "attempts:" in tap
        assert "pass_rate:" in tap
        assert "stability:" in tap
        assert "tolerance:" in tap


# ---------------------------------------------------------------------------
# 9. Integration: YAML → run → retry → assert → export
# ---------------------------------------------------------------------------


class TestRetryIntegration:
    def _make_suite_file(
        self,
        tmp_path: Path,
        retry: int = 1,
        tolerance: float = 1.0,
    ) -> Path:
        fix_path = _fixture_yaml(tmp_path)
        suite_path = tmp_path / "suite.yaml"
        suite_path.write_text(
            f"name: integration_suite\n"
            f"fixtures:\n  - {fix_path.name}\n"
            f"agent:\n  command: python -c \"import sys; print('done')\"\n"
            f"cases:\n"
            f"  - name: basic_case\n"
            f"    input: hi\n"
            f"    retry: {retry}\n"
            f"    tolerance: {tolerance}\n"
        )
        return suite_path

    def test_single_attempt_produces_no_retry_result(self, tmp_path: Path) -> None:
        from mcptest.testspec import load_test_suite
        from mcptest.cli.commands import _iter_suite_results

        suite_path = self._make_suite_file(tmp_path, retry=1)
        suite = load_test_suite(suite_path)
        results = list(_iter_suite_results(suite, suite_path))
        assert len(results) == 1
        assert results[0].retry_result is None

    def test_multi_attempt_produces_retry_result(self, tmp_path: Path) -> None:
        from mcptest.testspec import load_test_suite
        from mcptest.cli.commands import _iter_suite_results

        suite_path = self._make_suite_file(tmp_path, retry=3)
        suite = load_test_suite(suite_path)
        results = list(_iter_suite_results(suite, suite_path))
        assert len(results) == 1
        assert results[0].retry_result is not None
        assert len(results[0].retry_result.traces) == 3

    def test_retry_override_from_iter(self, tmp_path: Path) -> None:
        from mcptest.testspec import load_test_suite
        from mcptest.cli.commands import _iter_suite_results

        suite_path = self._make_suite_file(tmp_path, retry=1)
        suite = load_test_suite(suite_path)
        results = list(
            _iter_suite_results(suite, suite_path, retry_override=4)
        )
        assert results[0].retry_result is not None
        assert len(results[0].retry_result.traces) == 4

    def test_retry_injects_retry_result_in_metadata(self, tmp_path: Path) -> None:
        from mcptest.testspec import load_test_suite
        from mcptest.cli.commands import _iter_suite_results

        suite_path = self._make_suite_file(tmp_path, retry=2)
        suite = load_test_suite(suite_path)
        results = list(_iter_suite_results(suite, suite_path))
        r = results[0]
        assert "retry_result" in r.trace.metadata

    def test_stability_metric_computed_after_retry(self, tmp_path: Path) -> None:
        from mcptest.testspec import load_test_suite
        from mcptest.cli.commands import _iter_suite_results

        suite_path = self._make_suite_file(tmp_path, retry=3)
        suite = load_test_suite(suite_path)
        results = list(_iter_suite_results(suite, suite_path))
        r = results[0]
        stability_metrics = [m for m in r.metrics if m.name == "stability"]
        assert len(stability_metrics) == 1
        assert stability_metrics[0].score == 1.0  # All same outcome

    def test_junit_export_of_retry_result(self, tmp_path: Path) -> None:
        from mcptest.testspec import load_test_suite
        from mcptest.cli.commands import _iter_suite_results

        suite_path = self._make_suite_file(tmp_path, retry=2)
        suite = load_test_suite(suite_path)
        results = list(_iter_suite_results(suite, suite_path))
        xml_str = JUnitExporter().export(results)
        root = ET.fromstring(xml_str.split("\n", 1)[-1] if "?xml" in xml_str else xml_str)
        tc = root.find(".//testcase")
        assert tc is not None
        assert tc.attrib.get("attempts") == "2"

    def test_tap_export_of_retry_result(self, tmp_path: Path) -> None:
        from mcptest.testspec import load_test_suite
        from mcptest.cli.commands import _iter_suite_results

        suite_path = self._make_suite_file(tmp_path, retry=2)
        suite = load_test_suite(suite_path)
        results = list(_iter_suite_results(suite, suite_path))
        tap = TAPExporter().export(results)
        assert "1..2" in tap  # subtest plan for 2 attempts


# ---------------------------------------------------------------------------
# 10. Edge cases
# ---------------------------------------------------------------------------


class TestRetryEdgeCases:
    def test_retry_result_tolerance_boundary_exact(self) -> None:
        """pass_rate == tolerance should pass."""
        traces = [_trace() for _ in range(10)]
        outcomes = [True] * 7 + [False] * 3  # 0.7
        rr = RetryResult.from_attempts(traces, outcomes, 0.7)
        assert rr.passed is True

    def test_retry_result_tolerance_just_below(self) -> None:
        traces = [_trace() for _ in range(10)]
        outcomes = [True] * 6 + [False] * 4  # 0.6 < 0.7
        rr = RetryResult.from_attempts(traces, outcomes, 0.7)
        assert rr.passed is False

    def test_stability_metric_no_retry_key_in_metadata(self) -> None:
        from mcptest.metrics.impls import stability

        trace = _trace(metadata={"other_key": "value"})
        result = stability().compute(trace)
        assert result.score == 1.0

    def test_junit_no_retry_result_field_for_single(self) -> None:
        r = _passing_result()
        xml_str = JUnitExporter().export([r])
        root = ET.fromstring(xml_str.split("\n", 1)[-1] if "?xml" in xml_str else xml_str)
        tc = root.find(".//testcase")
        assert "attempts" not in tc.attrib

    def test_tap_no_subtests_for_single(self) -> None:
        r = _passing_result()
        tap = TAPExporter().export([r])
        assert "attempt 1" not in tap

    def test_retry_result_from_dict_empty_traces(self) -> None:
        """from_dict with empty traces should raise."""
        with pytest.raises(ValueError):
            RetryResult.from_dict({"traces": [], "attempt_results": [], "tolerance": 1.0})

    def test_stability_metric_returns_metric_result_type(self) -> None:
        from mcptest.metrics.impls import stability
        from mcptest.metrics.base import MetricResult

        result = stability().compute(_trace())
        assert isinstance(result, MetricResult)
        assert result.name == "stability"

    def test_case_result_to_dict_from_dict_no_retry_round_trip(self) -> None:
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[AssertionResult(passed=True, name="ok", message="ok")],
        )
        d = r.to_dict()
        restored = CaseResult.from_dict(d)
        assert restored.passed == r.passed
        assert restored.retry_result is None

    def test_run_with_retry_traces_are_independent(self, tmp_path: Path) -> None:
        """Each attempt should have a distinct trace_id."""
        runner = _mk_runner(tmp_path)
        rr = runner.run_with_retry("hi", retry=3)
        trace_ids = [t.trace_id for t in rr.traces]
        assert len(set(trace_ids)) == 3

    def test_retry_result_attempt_results_immutable(self) -> None:
        rr = RetryResult.from_attempts([_trace()], [True], 1.0)
        assert isinstance(rr.attempt_results, tuple)
        assert isinstance(rr.traces, tuple)
