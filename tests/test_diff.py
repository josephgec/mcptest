"""Tests for the trace diff engine."""

from __future__ import annotations

from mcptest.diff import (
    BaselineStore,
    Regression,
    RegressionKind,
    TraceDiff,
    baseline_id,
    diff_traces,
)
from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner.trace import Trace


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


def _trace(
    *,
    calls: list[RecordedCall] | None = None,
    output: str = "",
    duration_s: float = 1.0,
) -> Trace:
    return Trace(
        tool_calls=calls or [],
        output=output,
        duration_s=duration_s,
    )


class TestRegressionAndDiffObjects:
    def test_to_dict(self) -> None:
        r = Regression(kind="x", message="m", old=1, new=2, call_index=3)
        d = r.to_dict()
        assert d == {"kind": "x", "message": "m", "old": 1, "new": 2, "call_index": 3}

    def test_trace_diff_by_kind(self) -> None:
        d = TraceDiff(
            regressions=[
                Regression(kind="a", message=""),
                Regression(kind="b", message=""),
                Regression(kind="a", message=""),
            ]
        )
        assert len(d.by_kind("a")) == 2
        assert len(d.by_kind("b")) == 1
        assert d.has_regressions is True
        assert d.to_dict()["count"] == 3

    def test_empty_diff(self) -> None:
        d = TraceDiff()
        assert d.has_regressions is False
        assert d.to_dict()["count"] == 0


class TestIdenticalTraces:
    def test_no_regressions(self) -> None:
        calls = [_call("a", arguments={"x": 1}, result="ok")]
        baseline = _trace(calls=calls, output="out", duration_s=1.0)
        current = _trace(
            calls=[_call("a", arguments={"x": 1}, result="ok")],
            output="out",
            duration_s=1.0,
        )
        assert diff_traces(baseline, current).regressions == []


class TestToolSelection:
    def test_different_sequence_reports_selection_and_counts(self) -> None:
        baseline = _trace(calls=[_call("a"), _call("b")])
        current = _trace(calls=[_call("a"), _call("c")])
        diff = diff_traces(baseline, current)
        kinds = {r.kind for r in diff.regressions}
        assert RegressionKind.TOOL_SELECTION in kinds
        assert RegressionKind.TOOL_COUNT in kinds

    def test_extra_tool_call(self) -> None:
        baseline = _trace(calls=[_call("a")])
        current = _trace(calls=[_call("a"), _call("a")])
        diff = diff_traces(baseline, current)
        counts = diff.by_kind(RegressionKind.TOOL_COUNT)
        assert len(counts) == 1
        assert counts[0].old == 1
        assert counts[0].new == 2

    def test_removed_tool_call(self) -> None:
        baseline = _trace(calls=[_call("a"), _call("a")])
        current = _trace(calls=[_call("a")])
        diff = diff_traces(baseline, current)
        counts = diff.by_kind(RegressionKind.TOOL_COUNT)
        assert len(counts) == 1
        assert counts[0].old == 2
        assert counts[0].new == 1


class TestParameterAndResultDrift:
    def test_parameter_drift(self) -> None:
        baseline = _trace(calls=[_call("a", arguments={"x": 1})])
        current = _trace(calls=[_call("a", arguments={"x": 2})])
        diff = diff_traces(baseline, current)
        drifts = diff.by_kind(RegressionKind.PARAMETER_DRIFT)
        assert len(drifts) == 1
        assert drifts[0].call_index == 0

    def test_result_drift(self) -> None:
        baseline = _trace(calls=[_call("a", result={"ok": True})])
        current = _trace(calls=[_call("a", result={"ok": False})])
        diff = diff_traces(baseline, current)
        drifts = diff.by_kind(RegressionKind.RESULT_DRIFT)
        assert len(drifts) == 1

    def test_error_state_flip(self) -> None:
        baseline = _trace(calls=[_call("a")])
        current = _trace(calls=[_call("a", error="boom")])
        diff = diff_traces(baseline, current)
        errors = diff.by_kind(RegressionKind.ERROR)
        assert len(errors) == 1
        assert "boom" in errors[0].message


class TestLatency:
    def test_under_threshold_not_reported(self) -> None:
        baseline = _trace(duration_s=1.0)
        current = _trace(duration_s=1.2)  # 20% slower
        assert not diff_traces(baseline, current).by_kind(RegressionKind.LATENCY)

    def test_over_threshold_reported(self) -> None:
        baseline = _trace(duration_s=1.0)
        current = _trace(duration_s=2.0)  # 100% slower
        reg = diff_traces(baseline, current).by_kind(RegressionKind.LATENCY)
        assert len(reg) == 1

    def test_zero_baseline_ignored(self) -> None:
        baseline = _trace(duration_s=0)
        current = _trace(duration_s=5)
        assert not diff_traces(baseline, current).by_kind(RegressionKind.LATENCY)

    def test_custom_threshold(self) -> None:
        baseline = _trace(duration_s=1.0)
        current = _trace(duration_s=1.2)
        reg = diff_traces(
            baseline, current, latency_threshold_pct=10.0
        ).by_kind(RegressionKind.LATENCY)
        assert len(reg) == 1


class TestOutput:
    def test_output_differs(self) -> None:
        baseline = _trace(output="hello")
        current = _trace(output="world")
        reg = diff_traces(baseline, current).by_kind(RegressionKind.OUTPUT)
        assert len(reg) == 1

    def test_fuzzy_output_ignores_whitespace(self) -> None:
        baseline = _trace(output="hello\n")
        current = _trace(output="  hello  ")
        assert not diff_traces(baseline, current).regressions

    def test_strict_output(self) -> None:
        baseline = _trace(output="hello\n")
        current = _trace(output="hello")
        reg = diff_traces(
            baseline, current, fuzzy_output=False
        ).by_kind(RegressionKind.OUTPUT)
        assert len(reg) == 1


class TestBaselineId:
    def test_plain(self) -> None:
        assert baseline_id("suite", "case") == "suite__case"

    def test_special_chars_sanitized(self) -> None:
        bid = baseline_id("my suite", "has/special:chars")
        assert bid == "my_suite__has_special_chars"

    def test_empty_parts(self) -> None:
        assert baseline_id("", "") == "case"


class TestBaselineStore:
    def test_save_and_load(self, tmp_path) -> None:
        store = BaselineStore(tmp_path / "base")
        trace = _trace(calls=[_call("a")], output="x")
        path = store.save("suite", "case", trace)
        assert path.exists()
        loaded = store.load("suite", "case")
        assert loaded is not None
        assert loaded.tool_names == ["a"]
        assert loaded.output == "x"

    def test_load_missing_returns_none(self, tmp_path) -> None:
        store = BaselineStore(tmp_path / "base")
        assert store.load("x", "y") is None

    def test_exists(self, tmp_path) -> None:
        store = BaselineStore(tmp_path / "base")
        assert store.exists("x", "y") is False
        store.save("x", "y", _trace())
        assert store.exists("x", "y") is True

    def test_delete(self, tmp_path) -> None:
        store = BaselineStore(tmp_path / "base")
        store.save("x", "y", _trace())
        assert store.delete("x", "y") is True
        assert store.delete("x", "y") is False

    def test_list_ids(self, tmp_path) -> None:
        store = BaselineStore(tmp_path / "base")
        assert store.list_ids() == []
        store.save("a", "one", _trace())
        store.save("a", "two", _trace())
        assert len(store.list_ids()) == 2

    def test_clear(self, tmp_path) -> None:
        store = BaselineStore(tmp_path / "base")
        store.save("x", "y", _trace())
        store.clear()
        assert store.list_ids() == []

    def test_clear_missing_dir_safe(self, tmp_path) -> None:
        store = BaselineStore(tmp_path / "missing")
        store.clear()  # must not raise

    def test_list_ids_missing_dir(self, tmp_path) -> None:
        store = BaselineStore(tmp_path / "missing")
        assert store.list_ids() == []
