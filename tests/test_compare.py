"""Tests for the metric regression comparison engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcptest.compare import (
    DEFAULT_THRESHOLDS,
    ComparisonReport,
    MetricDelta,
    compare_traces,
)
from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner.trace import Trace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trace(tools: list[str], trace_id: str = "base") -> Trace:
    """Create a minimal Trace with the given tool call sequence."""
    calls = [
        RecordedCall(tool=t, arguments={}, result={"ok": True})
        for t in tools
    ]
    return Trace(trace_id=trace_id, tool_calls=calls)


def _make_error_trace(trace_id: str = "err") -> Trace:
    """Trace where the first call errors and there is no recovery."""
    calls = [
        RecordedCall(tool="a", arguments={}, error="boom"),
    ]
    return Trace(trace_id=trace_id, tool_calls=calls)


def _make_recovery_trace(trace_id: str = "rec") -> Trace:
    """Trace where an error is followed by a successful call (recovery)."""
    calls = [
        RecordedCall(tool="a", arguments={}, error="boom"),
        RecordedCall(tool="b", arguments={}, result={"ok": True}),
    ]
    return Trace(trace_id=trace_id, tool_calls=calls)


# ---------------------------------------------------------------------------
# MetricDelta
# ---------------------------------------------------------------------------


class TestMetricDelta:
    def test_delta_computed_on_init(self) -> None:
        d = MetricDelta(name="x", label="X", base_score=0.8, head_score=0.9)
        assert abs(d.delta - 0.1) < 1e-9

    def test_negative_delta(self) -> None:
        d = MetricDelta(name="x", label="X", base_score=0.9, head_score=0.7)
        assert abs(d.delta - (-0.2)) < 1e-9

    def test_regressed_when_drop_exceeds_threshold(self) -> None:
        d = MetricDelta(name="x", label="X", base_score=0.9, head_score=0.7, threshold=0.1)
        assert d.regressed is True

    def test_not_regressed_when_drop_equals_threshold(self) -> None:
        # delta == -threshold: NOT a regression (strictly less than)
        d = MetricDelta(name="x", label="X", base_score=0.8, head_score=0.7, threshold=0.1)
        assert d.regressed is False

    def test_not_regressed_when_drop_below_threshold(self) -> None:
        d = MetricDelta(name="x", label="X", base_score=0.8, head_score=0.75, threshold=0.1)
        assert d.regressed is False

    def test_not_regressed_when_improved(self) -> None:
        d = MetricDelta(name="x", label="X", base_score=0.5, head_score=0.9)
        assert d.regressed is False

    def test_custom_threshold(self) -> None:
        d = MetricDelta(name="x", label="X", base_score=1.0, head_score=0.8, threshold=0.15)
        assert d.regressed is True  # delta = -0.2 < -0.15

    def test_threshold_zero_any_drop_regresses(self) -> None:
        d = MetricDelta(name="x", label="X", base_score=0.9, head_score=0.89, threshold=0.0)
        assert d.regressed is True

    def test_to_dict_keys(self) -> None:
        d = MetricDelta(name="foo", label="Foo", base_score=0.6, head_score=0.5)
        out = d.to_dict()
        assert set(out.keys()) == {"name", "label", "base_score", "head_score", "delta", "regressed", "threshold"}

    def test_to_dict_values(self) -> None:
        d = MetricDelta(name="x", label="X", base_score=0.8, head_score=0.6, threshold=0.1)
        out = d.to_dict()
        assert out["name"] == "x"
        assert out["label"] == "X"
        assert abs(out["delta"] - (-0.2)) < 1e-9
        assert out["regressed"] is True
        assert out["threshold"] == 0.1

    def test_default_threshold_is_0_1(self) -> None:
        d = MetricDelta(name="x", label="X", base_score=0.0, head_score=0.0)
        assert d.threshold == 0.1


# ---------------------------------------------------------------------------
# ComparisonReport
# ---------------------------------------------------------------------------


class TestComparisonReport:
    def _make_report(
        self,
        *,
        base_id: str = "base",
        head_id: str = "head",
        deltas: list[MetricDelta] | None = None,
    ) -> ComparisonReport:
        if deltas is None:
            deltas = [
                MetricDelta(name="a", label="A", base_score=0.9, head_score=0.8),
                MetricDelta(name="b", label="B", base_score=0.7, head_score=0.7),
            ]
        return ComparisonReport(
            base_trace_id=base_id,
            head_trace_id=head_id,
            deltas=deltas,
        )

    def test_overall_passed_when_no_regressions(self) -> None:
        report = self._make_report()
        # delta = -0.1 for "a", threshold=0.1 — NOT a regression (delta == -threshold)
        assert report.overall_passed is True

    def test_overall_failed_when_regression_present(self) -> None:
        deltas = [
            MetricDelta(name="a", label="A", base_score=1.0, head_score=0.5),
        ]
        report = self._make_report(deltas=deltas)
        assert report.overall_passed is False

    def test_regressions_property(self) -> None:
        deltas = [
            MetricDelta(name="a", label="A", base_score=1.0, head_score=0.5),
            MetricDelta(name="b", label="B", base_score=0.8, head_score=0.8),
        ]
        report = self._make_report(deltas=deltas)
        regs = report.regressions
        assert len(regs) == 1
        assert regs[0].name == "a"

    def test_improvements_property(self) -> None:
        deltas = [
            MetricDelta(name="a", label="A", base_score=0.5, head_score=0.9),  # +0.4
            MetricDelta(name="b", label="B", base_score=0.8, head_score=0.83),  # +0.03, below 0.05
        ]
        report = self._make_report(deltas=deltas)
        imps = report.improvements
        assert len(imps) == 1
        assert imps[0].name == "a"

    def test_empty_deltas(self) -> None:
        report = self._make_report(deltas=[])
        assert report.overall_passed is True
        assert report.regressions == []
        assert report.improvements == []

    def test_to_dict_structure(self) -> None:
        report = self._make_report()
        out = report.to_dict()
        assert set(out.keys()) == {
            "base_trace_id", "head_trace_id", "deltas", "overall_passed", "regression_count"
        }

    def test_to_dict_regression_count(self) -> None:
        deltas = [
            MetricDelta(name="a", label="A", base_score=1.0, head_score=0.5),
            MetricDelta(name="b", label="B", base_score=1.0, head_score=0.5),
        ]
        report = self._make_report(deltas=deltas)
        out = report.to_dict()
        assert out["regression_count"] == 2
        assert out["overall_passed"] is False

    def test_to_dict_roundtrip_json(self) -> None:
        report = self._make_report()
        out = report.to_dict()
        text = json.dumps(out)
        data = json.loads(text)
        assert data["base_trace_id"] == "base"
        assert isinstance(data["deltas"], list)


# ---------------------------------------------------------------------------
# compare_traces — core logic
# ---------------------------------------------------------------------------


class TestCompareTraces:
    def test_identical_traces_no_regressions(self) -> None:
        trace = _make_trace(["a", "b", "c"])
        report = compare_traces(trace, trace)
        assert report.overall_passed is True
        assert report.regressions == []
        for d in report.deltas:
            assert abs(d.delta) < 1e-9

    def test_better_head_no_regressions(self) -> None:
        # Base has duplicate calls (lower tool_efficiency), head doesn't.
        base = _make_trace(["a", "a", "a"])
        head = _make_trace(["a", "b", "c"])
        report = compare_traces(base, head)
        assert report.overall_passed is True
        # tool_efficiency should improve
        eff_delta = next(d for d in report.deltas if d.name == "tool_efficiency")
        assert eff_delta.delta > 0

    def test_worse_head_produces_regressions(self) -> None:
        # Head has duplicates and errors, base doesn't.
        head = _make_trace(["a", "a", "a"], trace_id="head")  # low tool_efficiency
        base = _make_trace(["a", "b", "c"], trace_id="base")
        report = compare_traces(base, head)
        # tool_efficiency dropped
        eff = next(d for d in report.deltas if d.name == "tool_efficiency")
        assert eff.regressed is True
        assert report.overall_passed is False

    def test_all_metrics_present_in_report(self) -> None:
        base = _make_trace(["x"])
        head = _make_trace(["x"])
        report = compare_traces(base, head)
        names = {d.name for d in report.deltas}
        # At least the fixture-independent metrics must appear.
        assert "tool_efficiency" in names
        assert "redundancy" in names
        assert "error_recovery_rate" in names

    def test_custom_threshold_tighter(self) -> None:
        base = _make_trace(["a", "b"], trace_id="base")
        head = _make_trace(["a", "a"], trace_id="head")  # tool_efficiency drops from 1.0 to 0.5
        # With a very tight threshold (0.0) even a tiny drop is a regression.
        report = compare_traces(base, head, thresholds={"tool_efficiency": 0.0})
        eff = next(d for d in report.deltas if d.name == "tool_efficiency")
        assert eff.regressed is True

    def test_custom_threshold_looser(self) -> None:
        base = _make_trace(["a", "b"], trace_id="base")
        head = _make_trace(["a", "a"], trace_id="head")  # tool_efficiency drops 0.5
        # With a very loose threshold the drop doesn't trigger regression.
        report = compare_traces(base, head, thresholds={"tool_efficiency": 0.9})
        eff = next(d for d in report.deltas if d.name == "tool_efficiency")
        assert eff.regressed is False

    def test_empty_base_and_head(self) -> None:
        base = _make_trace([], trace_id="base")
        head = _make_trace([], trace_id="head")
        report = compare_traces(base, head)
        assert report.overall_passed is True
        for d in report.deltas:
            assert abs(d.delta) < 1e-9

    def test_single_tool_call(self) -> None:
        base = _make_trace(["tool"], trace_id="base")
        head = _make_trace(["tool"], trace_id="head")
        report = compare_traces(base, head)
        assert report.overall_passed is True

    def test_trace_ids_recorded(self) -> None:
        base = _make_trace([], trace_id="the-base")
        head = _make_trace([], trace_id="the-head")
        report = compare_traces(base, head)
        assert report.base_trace_id == "the-base"
        assert report.head_trace_id == "the-head"

    def test_error_recovery_regression(self) -> None:
        # Base has error with recovery, head has unrecovered error.
        base = _make_recovery_trace(trace_id="base")
        head = _make_error_trace(trace_id="head")
        report = compare_traces(base, head)
        err = next(
            (d for d in report.deltas if d.name == "error_recovery_rate"), None
        )
        assert err is not None
        assert err.delta < 0  # head scored worse

    def test_to_dict_is_json_serialisable(self) -> None:
        base = _make_trace(["a"])
        head = _make_trace(["a", "a"])
        report = compare_traces(base, head)
        text = json.dumps(report.to_dict())
        data = json.loads(text)
        assert "deltas" in data
        assert "overall_passed" in data


# ---------------------------------------------------------------------------
# DEFAULT_THRESHOLDS
# ---------------------------------------------------------------------------


class TestDefaultThresholds:
    def test_all_metrics_covered(self) -> None:
        from mcptest.metrics.base import METRICS
        for name in METRICS:
            assert name in DEFAULT_THRESHOLDS, f"{name} missing from DEFAULT_THRESHOLDS"

    def test_all_thresholds_are_positive(self) -> None:
        for name, t in DEFAULT_THRESHOLDS.items():
            assert t > 0, f"threshold for {name} must be positive"

    def test_all_thresholds_in_range(self) -> None:
        for name, t in DEFAULT_THRESHOLDS.items():
            assert 0 < t < 1.0, f"threshold {t} for {name} out of expected range"


# ---------------------------------------------------------------------------
# compare_traces with file-backed traces (integration)
# ---------------------------------------------------------------------------


class TestCompareTracesFromFiles:
    def test_save_and_compare_via_files(self, tmp_path: Path) -> None:
        base = _make_trace(["a", "b", "c"], trace_id="base")
        head = _make_trace(["a", "a"], trace_id="head")

        base_file = tmp_path / "base.json"
        head_file = tmp_path / "head.json"
        base.save(str(base_file))
        head.save(str(head_file))

        loaded_base = Trace.load(base_file)
        loaded_head = Trace.load(head_file)

        report = compare_traces(loaded_base, loaded_head)
        assert report.base_trace_id == "base"
        assert report.head_trace_id == "head"
        eff = next(d for d in report.deltas if d.name == "tool_efficiency")
        assert eff.regressed is True
