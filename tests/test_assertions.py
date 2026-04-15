"""Unit tests for the assertion library."""

from __future__ import annotations

import pytest

from mcptest.assertions import (
    ASSERTIONS,
    AssertionResult,
    McpTestAssertionError,
    all_of,
    any_of,
    assert_all,
    check_all,
    completes_within_s,
    error_handled,
    max_tool_calls,
    metric_above,
    metric_below,
    no_errors,
    none_of,
    output_contains,
    output_matches,
    param_matches,
    param_schema_valid,
    parse_assertion,
    parse_assertions,
    tool_call_count,
    tool_called,
    tool_not_called,
    tool_order,
    trajectory_matches,
    weighted_score,
)
from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner.trace import Trace


def _trace(
    *,
    calls: list[RecordedCall] | None = None,
    output: str = "",
    duration_s: float = 0.0,
    exit_code: int = 0,
    agent_error: str | None = None,
) -> Trace:
    return Trace(
        input="",
        output=output,
        tool_calls=calls or [],
        duration_s=duration_s,
        exit_code=exit_code,
        agent_error=agent_error,
    )


def _call(
    tool: str,
    *,
    arguments: dict[str, object] | None = None,
    result: object | None = None,
    error: str | None = None,
) -> RecordedCall:
    return RecordedCall(
        tool=tool,
        arguments=arguments or {},
        result=result,
        error=error,
    )


class TestAssertionResult:
    def test_bool(self) -> None:
        assert bool(AssertionResult(passed=True, name="x", message=""))
        assert not bool(AssertionResult(passed=False, name="x", message=""))

    def test_to_dict(self) -> None:
        r = AssertionResult(
            passed=True, name="x", message="m", details={"k": 1}
        )
        d = r.to_dict()
        assert d["passed"] is True
        assert d["details"] == {"k": 1}


class TestToolSelectionAssertions:
    def test_tool_called_passes(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        r = tool_called("a").check(t)
        assert r.passed

    def test_tool_called_fails(self) -> None:
        t = _trace(calls=[_call("b")])
        r = tool_called("a").check(t)
        assert not r.passed
        assert "not called" in r.message

    def test_tool_not_called_passes(self) -> None:
        t = _trace(calls=[_call("a")])
        assert tool_not_called("b").check(t).passed

    def test_tool_not_called_fails(self) -> None:
        t = _trace(calls=[_call("a"), _call("a")])
        r = tool_not_called("a").check(t)
        assert not r.passed
        assert "2" in r.message

    def test_tool_call_count_exact(self) -> None:
        t = _trace(calls=[_call("a"), _call("a"), _call("b")])
        assert tool_call_count("a", 2).check(t).passed
        assert not tool_call_count("a", 1).check(t).passed
        assert tool_call_count("b", 1).check(t).passed
        assert tool_call_count("missing", 0).check(t).passed

    def test_max_tool_calls(self) -> None:
        t = _trace(calls=[_call("a"), _call("b"), _call("c")])
        assert max_tool_calls(5).check(t).passed
        assert max_tool_calls(3).check(t).passed
        assert not max_tool_calls(2).check(t).passed


class TestParamMatches:
    def test_exact_value(self) -> None:
        t = _trace(calls=[_call("x", arguments={"p": "foo"})])
        assert param_matches("x", "p", value="foo").check(t).passed
        assert not param_matches("x", "p", value="bar").check(t).passed

    def test_exact_value_none(self) -> None:
        # value=None is indistinguishable from unset in a dataclass default —
        # so we expose value explicitly as a sentinel.
        t = _trace(calls=[_call("x", arguments={"p": "foo"})])
        r = param_matches("x", "p", value="foo").check(t)
        assert r.passed

    def test_contains(self) -> None:
        t = _trace(calls=[_call("x", arguments={"title": "bug: 500 on safari"})])
        assert param_matches("x", "title", contains="500").check(t).passed
        assert not param_matches("x", "title", contains="404").check(t).passed

    def test_regex(self) -> None:
        t = _trace(calls=[_call("x", arguments={"url": "https://a.b/c"})])
        assert param_matches("x", "url", regex=r"^https://").check(t).passed
        assert not param_matches("x", "url", regex=r"^ftp://").check(t).passed

    def test_regex_invalid(self) -> None:
        t = _trace(calls=[_call("x", arguments={"url": "x"})])
        assert not param_matches("x", "url", regex="[unclosed").check(t).passed

    def test_requires_exactly_one_condition(self) -> None:
        t = _trace(calls=[_call("x", arguments={"p": "v"})])
        r = param_matches("x", "p").check(t)
        assert not r.passed
        assert "exactly one" in r.message

        r = param_matches("x", "p", value="v", contains="v").check(t)
        assert not r.passed

    def test_tool_never_called(self) -> None:
        t = _trace(calls=[])
        r = param_matches("x", "p", value="v").check(t)
        assert not r.passed
        assert "never called" in r.message

    def test_param_absent(self) -> None:
        t = _trace(calls=[_call("x", arguments={"other": "v"})])
        r = param_matches("x", "p", value="v").check(t)
        assert not r.passed
        assert "not present" in r.message

    def test_call_index(self) -> None:
        t = _trace(
            calls=[
                _call("x", arguments={"p": "a"}),
                _call("x", arguments={"p": "b"}),
            ]
        )
        assert param_matches("x", "p", value="a", call_index=0).check(t).passed
        assert not param_matches("x", "p", value="a", call_index=1).check(t).passed

    def test_call_index_out_of_range(self) -> None:
        t = _trace(calls=[_call("x", arguments={"p": "v"})])
        r = param_matches("x", "p", value="v", call_index=5).check(t)
        assert not r.passed
        assert "no call #5" in r.message

    def test_any_of_multiple_calls_matches(self) -> None:
        t = _trace(
            calls=[
                _call("x", arguments={"p": "a"}),
                _call("x", arguments={"p": "target"}),
            ]
        )
        assert param_matches("x", "p", value="target").check(t).passed

    def test_shows_observed_values_on_failure(self) -> None:
        t = _trace(
            calls=[
                _call("x", arguments={"p": "a"}),
                _call("x", arguments={"p": "b"}),
            ]
        )
        r = param_matches("x", "p", value="c").check(t)
        assert not r.passed
        assert "'a'" in r.message and "'b'" in r.message


class TestParamSchemaValid:
    _SCHEMA = {
        "type": "object",
        "properties": {"x": {"type": "number"}},
        "required": ["x"],
    }

    def test_valid(self) -> None:
        t = _trace(calls=[_call("f", arguments={"x": 1})])
        assert param_schema_valid("f", self._SCHEMA).check(t).passed

    def test_invalid(self) -> None:
        t = _trace(calls=[_call("f", arguments={"x": "string"})])
        r = param_schema_valid("f", self._SCHEMA).check(t)
        assert not r.passed

    def test_missing_required(self) -> None:
        t = _trace(calls=[_call("f", arguments={})])
        r = param_schema_valid("f", self._SCHEMA).check(t)
        assert not r.passed

    def test_tool_never_called(self) -> None:
        t = _trace(calls=[])
        assert not param_schema_valid("f", self._SCHEMA).check(t).passed


class TestOrdering:
    def test_tool_order_exact(self) -> None:
        t = _trace(calls=[_call("a"), _call("b"), _call("c")])
        assert tool_order(["a", "b"]).check(t).passed
        assert tool_order(["b", "c"]).check(t).passed
        assert tool_order(["a", "b", "c"]).check(t).passed

    def test_tool_order_missing(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        assert not tool_order(["b", "a"]).check(t).passed
        assert not tool_order(["a", "c"]).check(t).passed

    def test_tool_order_empty_trivially_true(self) -> None:
        t = _trace(calls=[_call("a")])
        assert tool_order([]).check(t).passed

    def test_tool_order_does_not_match_empty_trace(self) -> None:
        t = _trace(calls=[])
        assert not tool_order(["a"]).check(t).passed

    def test_trajectory_matches_exact(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        assert trajectory_matches(["a", "b"]).check(t).passed

    def test_trajectory_matches_differs(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        r = trajectory_matches(["a"]).check(t)
        assert not r.passed
        assert "expected" in r.message


class TestPerformanceAndOutput:
    def test_completes_within_s(self) -> None:
        t = _trace(duration_s=1.5)
        assert completes_within_s(2).check(t).passed
        assert not completes_within_s(1).check(t).passed

    def test_output_contains(self) -> None:
        t = _trace(output="Created issue #42 successfully")
        assert output_contains("#42").check(t).passed
        assert not output_contains("missing").check(t).passed

    def test_output_contains_case_insensitive(self) -> None:
        t = _trace(output="Hello World")
        assert output_contains("world", case_sensitive=False).check(t).passed
        assert not output_contains("world").check(t).passed

    def test_output_matches(self) -> None:
        t = _trace(output="Issue #42 created")
        assert output_matches(r"#\d+").check(t).passed
        assert not output_matches(r"error").check(t).passed

    def test_output_matches_invalid_regex(self) -> None:
        t = _trace(output="anything")
        r = output_matches("[unclosed").check(t)
        assert not r.passed
        assert "invalid regex" in r.message


class TestErrorAssertions:
    def test_no_errors_passes(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        assert no_errors().check(t).passed

    def test_no_errors_fails(self) -> None:
        t = _trace(calls=[_call("a", error="boom"), _call("b")])
        r = no_errors().check(t)
        assert not r.passed
        assert "boom" in r.message

    def test_error_handled_success(self) -> None:
        t = _trace(
            calls=[
                _call("a", error="rate limit exceeded"),
                _call("a"),
            ],
            exit_code=0,
        )
        assert error_handled("rate limit").check(t).passed

    def test_error_handled_not_raised(self) -> None:
        t = _trace(calls=[_call("a")], exit_code=0)
        r = error_handled("rate limit").check(t)
        assert not r.passed
        assert "never raised" in r.message

    def test_error_handled_agent_failed(self) -> None:
        t = _trace(
            calls=[_call("a", error="rate limit")],
            exit_code=1,
        )
        r = error_handled("rate limit").check(t)
        assert not r.passed
        assert "did not complete" in r.message

    def test_error_handled_exact_match(self) -> None:
        t = _trace(calls=[_call("a", error="exact")], exit_code=0)
        assert error_handled("exact").check(t).passed


class TestAssertHelpers:
    def test_assert_raises_on_failure(self) -> None:
        t = _trace(calls=[_call("b")])
        with pytest.raises(McpTestAssertionError):
            tool_called("a").assert_(t)

    def test_assert_does_not_raise_on_pass(self) -> None:
        t = _trace(calls=[_call("a")])
        tool_called("a").assert_(t)

    def test_check_all_returns_results(self) -> None:
        t = _trace(calls=[_call("a")])
        results = check_all(
            [tool_called("a"), tool_called("b")], t
        )
        assert len(results) == 2
        assert results[0].passed
        assert not results[1].passed

    def test_assert_all_raises_on_first_failure(self) -> None:
        t = _trace(calls=[_call("a")])
        with pytest.raises(McpTestAssertionError) as exc_info:
            assert_all(
                [tool_called("a"), tool_called("missing"), tool_called("other")],
                t,
            )
        assert "missing" in str(exc_info.value)

    def test_assert_all_passes_when_all_pass(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        assert_all([tool_called("a"), tool_called("b")], t)


class TestYamlParsing:
    def test_scalar_form(self) -> None:
        a = parse_assertion({"tool_called": "create_issue"})
        assert isinstance(a, tool_called)
        assert a.tool == "create_issue"

    def test_numeric_scalar(self) -> None:
        a = parse_assertion({"max_tool_calls": 3})
        assert isinstance(a, max_tool_calls)
        assert a.limit == 3

    def test_list_form(self) -> None:
        a = parse_assertion({"tool_order": ["a", "b"]})
        assert isinstance(a, tool_order)
        assert a.sequence == ["a", "b"]

    def test_dict_form(self) -> None:
        a = parse_assertion(
            {"param_matches": {"tool": "x", "param": "y", "contains": "z"}}
        )
        assert isinstance(a, param_matches)
        assert a.tool == "x"
        assert a.contains == "z"

    def test_non_mapping_rejected(self) -> None:
        with pytest.raises(ValueError, match="single-key"):
            parse_assertion(["tool_called", "x"])  # type: ignore[arg-type]

    def test_multi_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="single-key"):
            parse_assertion({"tool_called": "x", "max_tool_calls": 3})

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown assertion"):
            parse_assertion({"no_such_assertion": 1})

    def test_bad_args_rejected(self) -> None:
        with pytest.raises(ValueError, match="bad arguments"):
            parse_assertion({"tool_called": {"not_tool": "x"}})

    def test_parse_assertions_list(self) -> None:
        a = parse_assertions(
            [{"tool_called": "x"}, {"max_tool_calls": 5}]
        )
        assert len(a) == 2
        assert isinstance(a[0], tool_called)
        assert isinstance(a[1], max_tool_calls)

    def test_no_errors_yaml_form(self) -> None:
        a = parse_assertion({"no_errors": True})
        assert isinstance(a, no_errors)
        t = _trace()
        assert a.check(t).passed


class TestMetricGatedAssertions:
    """Tests for metric_above and metric_below."""

    def test_metric_above_passes(self) -> None:
        # tool_efficiency = unique/total; 3 unique / 3 calls = 1.0
        t = _trace(calls=[_call("a"), _call("b"), _call("c")])
        r = metric_above("tool_efficiency", 0.8).check(t)
        assert r.passed
        assert "tool_efficiency" in r.message
        assert r.details["score"] == pytest.approx(1.0)

    def test_metric_above_fails(self) -> None:
        # tool_efficiency = 1/3 ≈ 0.333 (only one unique tool used 3 times)
        t = _trace(calls=[_call("a"), _call("a"), _call("a")])
        r = metric_above("tool_efficiency", 0.8).check(t)
        assert not r.passed
        assert "< threshold" in r.message

    def test_metric_above_unknown_metric(self) -> None:
        t = _trace()
        r = metric_above("no_such_metric", 0.5).check(t)
        assert not r.passed
        assert "unknown metric" in r.message

    def test_metric_below_passes(self) -> None:
        # redundancy = 1 - (dupes/total); no dupes → 1.0, but threshold 1.0
        t = _trace(calls=[_call("a"), _call("b")])
        r = metric_below("tool_efficiency", 1.0).check(t)
        assert r.passed

    def test_metric_below_fails(self) -> None:
        # tool_efficiency = 1.0 for unique calls, threshold = 0.5
        t = _trace(calls=[_call("a"), _call("b")])
        r = metric_below("tool_efficiency", 0.5).check(t)
        assert not r.passed
        assert "> threshold" in r.message

    def test_metric_below_unknown_metric(self) -> None:
        t = _trace()
        r = metric_below("no_such_metric", 0.5).check(t)
        assert not r.passed
        assert "unknown metric" in r.message

    def test_metric_above_yaml_round_trip(self) -> None:
        a = parse_assertion({"metric_above": {"metric": "tool_efficiency", "threshold": 0.5}})
        assert isinstance(a, metric_above)
        assert a.metric == "tool_efficiency"
        assert a.threshold == 0.5

    def test_metric_below_yaml_round_trip(self) -> None:
        a = parse_assertion({"metric_below": {"metric": "redundancy", "threshold": 0.3}})
        assert isinstance(a, metric_below)
        assert a.metric == "redundancy"
        assert a.threshold == 0.3

    def test_metric_above_empty_trace(self) -> None:
        # Empty trace: tool_efficiency = 0/0, metric returns 1.0 (no calls)
        t = _trace()
        r = metric_above("tool_efficiency", 0.5).check(t)
        # 1.0 >= 0.5, should pass
        assert r.passed


class TestAssertionCombinators:
    """Tests for all_of, any_of, none_of."""

    def test_all_of_passes_when_all_pass(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        a = all_of([{"tool_called": "a"}, {"tool_called": "b"}])
        r = a.check(t)
        assert r.passed
        assert "2" in r.message

    def test_all_of_fails_when_one_fails(self) -> None:
        t = _trace(calls=[_call("a")])
        a = all_of([{"tool_called": "a"}, {"tool_called": "missing"}])
        r = a.check(t)
        assert not r.passed
        assert "failed" in r.message
        assert r.details["failed_count"] == 1

    def test_all_of_fails_when_all_fail(self) -> None:
        t = _trace(calls=[])
        a = all_of([{"tool_called": "x"}, {"tool_called": "y"}])
        r = a.check(t)
        assert not r.passed
        assert r.details["failed_count"] == 2

    def test_all_of_empty_list_passes(self) -> None:
        t = _trace()
        a = all_of([])
        assert a.check(t).passed

    def test_any_of_passes_when_one_passes(self) -> None:
        t = _trace(calls=[_call("b")])
        a = any_of([{"tool_called": "a"}, {"tool_called": "b"}])
        r = a.check(t)
        assert r.passed
        assert r.details["passed_count"] == 1

    def test_any_of_fails_when_none_pass(self) -> None:
        t = _trace(calls=[])
        a = any_of([{"tool_called": "a"}, {"tool_called": "b"}])
        r = a.check(t)
        assert not r.passed
        assert "none of 2" in r.message

    def test_none_of_passes_when_none_pass(self) -> None:
        t = _trace(calls=[_call("safe")])
        a = none_of([{"tool_called": "dangerous"}, {"output_contains": "ERROR"}])
        r = a.check(t)
        assert r.passed
        assert "none of 2" in r.message

    def test_none_of_fails_when_one_passes(self) -> None:
        t = _trace(calls=[_call("dangerous")])
        a = none_of([{"tool_called": "dangerous"}])
        r = a.check(t)
        assert not r.passed
        assert "unexpectedly passed" in r.message

    def test_combinators_yaml_round_trip(self) -> None:
        a = parse_assertion({"all_of": [{"tool_called": "x"}, {"max_tool_calls": 5}]})
        assert isinstance(a, all_of)
        assert len(a.assertions) == 2

        b = parse_assertion({"any_of": [{"tool_called": "x"}]})
        assert isinstance(b, any_of)

        c = parse_assertion({"none_of": [{"tool_called": "bad"}]})
        assert isinstance(c, none_of)

    def test_nested_combinator(self) -> None:
        # all_of wrapping an any_of
        t = _trace(calls=[_call("a")])
        a = all_of([
            {"tool_called": "a"},
            {"any_of": [{"tool_called": "a"}, {"tool_called": "z"}]},
        ])
        assert a.check(t).passed


class TestWeightedScore:
    """Tests for the weighted_score assertion."""

    def test_passes_when_above_threshold(self) -> None:
        # All unique calls → tool_efficiency = 1.0; no dupes → redundancy = 1.0
        t = _trace(calls=[_call("a"), _call("b"), _call("c")])
        a = weighted_score(threshold=0.5, weights={"tool_efficiency": 1.0, "redundancy": 1.0})
        r = a.check(t)
        assert r.passed
        assert r.details["composite"] >= 0.5

    def test_fails_when_below_threshold(self) -> None:
        # All same tool → tool_efficiency = 1/3 ≈ 0.333; redundancy = 0 (dupes)
        t = _trace(calls=[_call("a"), _call("a"), _call("a")])
        a = weighted_score(threshold=0.9, weights={"tool_efficiency": 1.0, "redundancy": 1.0})
        r = a.check(t)
        assert not r.passed
        assert "< threshold" in r.message

    def test_unknown_metric_fails(self) -> None:
        t = _trace()
        a = weighted_score(threshold=0.5, weights={"no_such_metric": 1.0})
        r = a.check(t)
        assert not r.passed
        assert "unknown metric" in r.message

    def test_empty_weights_fails(self) -> None:
        t = _trace()
        a = weighted_score(threshold=0.5, weights={})
        r = a.check(t)
        assert not r.passed
        assert "at least one" in r.message

    def test_weighted_score_details(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        a = weighted_score(threshold=0.5, weights={"tool_efficiency": 0.7, "redundancy": 0.3})
        r = a.check(t)
        assert "scores" in r.details
        assert "weights" in r.details
        assert "composite" in r.details

    def test_weighted_score_yaml_round_trip(self) -> None:
        a = parse_assertion({
            "weighted_score": {
                "threshold": 0.75,
                "weights": {"tool_efficiency": 0.5, "redundancy": 0.5},
            }
        })
        assert isinstance(a, weighted_score)
        assert a.threshold == 0.75
        assert a.weights == {"tool_efficiency": 0.5, "redundancy": 0.5}

    def test_zero_weight_fails(self) -> None:
        t = _trace()
        a = weighted_score(threshold=0.5, weights={"tool_efficiency": 0.0})
        r = a.check(t)
        assert not r.passed
        assert "total weight is zero" in r.message


class TestRegistry:
    def test_all_core_registered(self) -> None:
        expected = {
            "tool_called",
            "tool_not_called",
            "tool_call_count",
            "param_matches",
            "param_schema_valid",
            "tool_order",
            "trajectory_matches",
            "max_tool_calls",
            "completes_within_s",
            "output_contains",
            "output_matches",
            "no_errors",
            "error_handled",
            "metric_above",
            "metric_below",
            "all_of",
            "any_of",
            "none_of",
            "weighted_score",
        }
        assert expected.issubset(ASSERTIONS.keys())

    def test_duplicate_registration_rejected(self) -> None:
        from mcptest.assertions.base import register_assertion

        class _Dup:
            yaml_key = "tool_called"

        with pytest.raises(ValueError, match="already registered"):
            register_assertion(_Dup)

    def test_missing_yaml_key_rejected(self) -> None:
        from mcptest.assertions.base import register_assertion

        class _NoKey:
            pass

        with pytest.raises(TypeError, match="yaml_key"):
            register_assertion(_NoKey)
