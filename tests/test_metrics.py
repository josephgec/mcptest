"""Unit tests for the metrics library."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mcptest.cli.main import main
from mcptest.fixtures.models import Fixture, Response, ServerSpec, ToolSpec
from mcptest.metrics import (
    METRICS,
    MetricResult,
    compute_all,
    error_recovery_rate,
    redundancy,
    schema_compliance,
    tool_coverage,
    tool_efficiency,
    trajectory_similarity,
)
from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner.trace import Trace


# ---------------------------------------------------------------------------
# Helper constructors
# ---------------------------------------------------------------------------


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


def _fixture(tools: list[tuple[str, dict]] | None = None) -> Fixture:
    """Build a minimal Fixture with the given tools.

    Each tool is a (name, input_schema) tuple.
    """
    tool_specs = []
    for name, schema in (tools or []):
        tool_specs.append(
            ToolSpec(
                name=name,
                input_schema=schema,
                responses=[Response(**{"return": {"ok": True}})],
            )
        )
    return Fixture(
        server=ServerSpec(name="test-server"),
        tools=tool_specs,
    )


# ---------------------------------------------------------------------------
# TestMetricResult
# ---------------------------------------------------------------------------


class TestMetricResult:
    def test_to_dict(self) -> None:
        r = MetricResult(name="x", score=0.75, label="X Metric", details={"k": 1})
        d = r.to_dict()
        assert d["name"] == "x"
        assert d["score"] == 0.75
        assert d["label"] == "X Metric"
        assert d["details"] == {"k": 1}

    def test_score_range_typical(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        for result in compute_all(t):
            assert 0.0 <= result.score <= 1.0

    def test_frozen(self) -> None:
        r = MetricResult(name="x", score=0.5, label="X")
        with pytest.raises((AttributeError, TypeError)):
            r.score = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestToolEfficiency
# ---------------------------------------------------------------------------


class TestToolEfficiency:
    def test_all_unique(self) -> None:
        t = _trace(calls=[_call("a"), _call("b"), _call("c")])
        r = tool_efficiency().compute(t)
        assert r.score == pytest.approx(1.0)
        assert r.details["unique"] == 3
        assert r.details["total"] == 3

    def test_all_same(self) -> None:
        t = _trace(calls=[_call("a"), _call("a"), _call("a")])
        r = tool_efficiency().compute(t)
        assert r.score == pytest.approx(1 / 3)
        assert r.details["unique"] == 1
        assert r.details["repeated"] == ["a"]

    def test_empty_trace(self) -> None:
        t = _trace(calls=[])
        r = tool_efficiency().compute(t)
        assert r.score == pytest.approx(1.0)
        assert r.details["total"] == 0

    def test_some_repeats(self) -> None:
        # [a, b, a, c] → 3 unique / 4 total = 0.75
        t = _trace(calls=[_call("a"), _call("b"), _call("a"), _call("c")])
        r = tool_efficiency().compute(t)
        assert r.score == pytest.approx(0.75)
        assert r.details["unique"] == 3
        assert r.details["total"] == 4
        assert "a" in r.details["repeated"]

    def test_name_and_label(self) -> None:
        r = tool_efficiency().compute(_trace())
        assert r.name == "tool_efficiency"
        assert r.label == "Tool Efficiency"


# ---------------------------------------------------------------------------
# TestRedundancy
# ---------------------------------------------------------------------------


class TestRedundancy:
    def test_no_duplicates(self) -> None:
        t = _trace(calls=[_call("a", arguments={"x": 1}), _call("b", arguments={"y": 2})])
        r = redundancy().compute(t)
        assert r.score == pytest.approx(1.0)
        assert r.details["duplicate_count"] == 0

    def test_all_duplicates(self) -> None:
        # [a(x), a(x), a(x)] → 2 dups of 3 total → 1 - 2/3 ≈ 0.333
        t = _trace(calls=[_call("a", arguments={"q": "x"})] * 3)
        r = redundancy().compute(t)
        assert r.score == pytest.approx(1 / 3)
        assert r.details["duplicate_count"] == 2

    def test_same_tool_different_args(self) -> None:
        # Same tool name but different arguments — not redundant
        t = _trace(
            calls=[
                _call("a", arguments={"q": "x"}),
                _call("a", arguments={"q": "y"}),
            ]
        )
        r = redundancy().compute(t)
        assert r.score == pytest.approx(1.0)
        assert r.details["duplicate_count"] == 0

    def test_empty_trace(self) -> None:
        t = _trace(calls=[])
        r = redundancy().compute(t)
        assert r.score == pytest.approx(1.0)

    def test_single_call(self) -> None:
        t = _trace(calls=[_call("a")])
        r = redundancy().compute(t)
        assert r.score == pytest.approx(1.0)

    def test_mixed(self) -> None:
        # [a(x), a(x), b(z)] → 1 dup of 3 → 1 - 1/3 ≈ 0.667
        t = _trace(
            calls=[
                _call("a", arguments={"q": "x"}),
                _call("a", arguments={"q": "x"}),
                _call("b", arguments={"q": "z"}),
            ]
        )
        r = redundancy().compute(t)
        assert r.score == pytest.approx(2 / 3)
        assert r.details["duplicate_count"] == 1
        assert r.details["duplicated_calls"] == ["a"]

    def test_name_and_label(self) -> None:
        r = redundancy().compute(_trace())
        assert r.name == "redundancy"
        assert r.label == "Non-Redundancy"


# ---------------------------------------------------------------------------
# TestErrorRecoveryRate
# ---------------------------------------------------------------------------


class TestErrorRecoveryRate:
    def test_no_errors(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        r = error_recovery_rate().compute(t)
        assert r.score == pytest.approx(1.0)
        assert r.details["error_count"] == 0

    def test_error_then_success(self) -> None:
        t = _trace(calls=[_call("a", error="boom"), _call("b")])
        r = error_recovery_rate().compute(t)
        assert r.score == pytest.approx(1.0)
        assert r.details["recovered"] == 1

    def test_error_at_end(self) -> None:
        t = _trace(calls=[_call("a"), _call("b", error="fail")])
        r = error_recovery_rate().compute(t)
        assert r.score == pytest.approx(0.0)
        assert r.details["unrecovered_indices"] == [1]

    def test_mixed(self) -> None:
        # error, success, error → 1 recovered, 1 unrecovered → 0.5
        t = _trace(
            calls=[
                _call("a", error="err1"),
                _call("b"),
                _call("c", error="err2"),
            ]
        )
        r = error_recovery_rate().compute(t)
        assert r.score == pytest.approx(0.5)
        assert r.details["recovered"] == 1
        assert r.details["unrecovered_indices"] == [2]

    def test_empty_trace(self) -> None:
        t = _trace(calls=[])
        r = error_recovery_rate().compute(t)
        assert r.score == pytest.approx(1.0)

    def test_all_errors_no_success(self) -> None:
        t = _trace(calls=[_call("a", error="e1"), _call("b", error="e2")])
        r = error_recovery_rate().compute(t)
        assert r.score == pytest.approx(0.0)
        assert r.details["recovered"] == 0

    def test_name_and_label(self) -> None:
        r = error_recovery_rate().compute(_trace())
        assert r.name == "error_recovery_rate"
        assert r.label == "Error Recovery Rate"


# ---------------------------------------------------------------------------
# TestTrajectorySimilarity
# ---------------------------------------------------------------------------


class TestTrajectorySimilarity:
    def test_identical(self) -> None:
        t = _trace(calls=[_call("a"), _call("b"), _call("c")])
        ref = _trace(calls=[_call("a"), _call("b"), _call("c")])
        r = trajectory_similarity().compute(t, reference=ref)
        assert r.score == pytest.approx(1.0)
        assert r.details["distance"] == 0

    def test_completely_different(self) -> None:
        t = _trace(calls=[_call("a")])
        ref = _trace(calls=[_call("b")])
        r = trajectory_similarity().compute(t, reference=ref)
        assert r.score == pytest.approx(0.0)
        assert r.details["distance"] == 1

    def test_one_insertion(self) -> None:
        # [a, b] vs [a, c, b] → edit distance 1, max_len 3 → score ≈ 0.667
        t = _trace(calls=[_call("a"), _call("b")])
        ref = _trace(calls=[_call("a"), _call("c"), _call("b")])
        r = trajectory_similarity().compute(t, reference=ref)
        assert r.score == pytest.approx(2 / 3)
        assert r.details["distance"] == 1
        assert r.details["max_length"] == 3

    def test_no_reference(self) -> None:
        t = _trace(calls=[_call("a")])
        r = trajectory_similarity().compute(t, reference=None)
        assert r.score == pytest.approx(1.0)
        assert "no reference" in r.details.get("note", "")

    def test_both_empty(self) -> None:
        t = _trace(calls=[])
        ref = _trace(calls=[])
        r = trajectory_similarity().compute(t, reference=ref)
        assert r.score == pytest.approx(1.0)

    def test_one_empty(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        ref = _trace(calls=[])
        r = trajectory_similarity().compute(t, reference=ref)
        assert r.score == pytest.approx(0.0)

    def test_name_and_label(self) -> None:
        r = trajectory_similarity().compute(_trace())
        assert r.name == "trajectory_similarity"
        assert r.label == "Trajectory Similarity"


# ---------------------------------------------------------------------------
# TestSchemaCompliance
# ---------------------------------------------------------------------------


_SIMPLE_SCHEMA: dict = {
    "type": "object",
    "properties": {"x": {"type": "number"}},
    "required": ["x"],
}


class TestSchemaCompliance:
    def test_all_valid(self) -> None:
        t = _trace(calls=[_call("f", arguments={"x": 1}), _call("f", arguments={"x": 2})])
        fix = _fixture([("f", _SIMPLE_SCHEMA)])
        r = schema_compliance().compute(t, fixtures=[fix])
        assert r.score == pytest.approx(1.0)
        assert r.details["invalid"] == 0

    def test_one_invalid(self) -> None:
        t = _trace(
            calls=[
                _call("f", arguments={"x": 1}),
                _call("f", arguments={"x": "not-a-number"}),
            ]
        )
        fix = _fixture([("f", _SIMPLE_SCHEMA)])
        r = schema_compliance().compute(t, fixtures=[fix])
        assert r.score == pytest.approx(0.5)
        assert r.details["invalid"] == 1
        assert len(r.details["violations"]) == 1

    def test_no_fixtures(self) -> None:
        t = _trace(calls=[_call("f", arguments={"x": 1})])
        r = schema_compliance().compute(t, fixtures=None)
        assert r.score == pytest.approx(1.0)
        assert "note" in r.details

    def test_tool_not_in_fixture(self) -> None:
        # Tool "g" is not declared in the fixture — treated as compliant.
        t = _trace(calls=[_call("g", arguments={"anything": True})])
        fix = _fixture([("f", _SIMPLE_SCHEMA)])
        r = schema_compliance().compute(t, fixtures=[fix])
        assert r.score == pytest.approx(1.0)
        assert r.details["invalid"] == 0

    def test_empty_trace(self) -> None:
        t = _trace(calls=[])
        fix = _fixture([("f", _SIMPLE_SCHEMA)])
        r = schema_compliance().compute(t, fixtures=[fix])
        assert r.score == pytest.approx(1.0)

    def test_name_and_label(self) -> None:
        r = schema_compliance().compute(_trace())
        assert r.name == "schema_compliance"
        assert r.label == "Schema Compliance"


# ---------------------------------------------------------------------------
# TestToolCoverage
# ---------------------------------------------------------------------------


class TestToolCoverage:
    def test_all_tools_used(self) -> None:
        t = _trace(calls=[_call("a"), _call("b"), _call("c")])
        fix = _fixture([("a", {}), ("b", {}), ("c", {})])
        r = tool_coverage().compute(t, fixtures=[fix])
        assert r.score == pytest.approx(1.0)
        assert r.details["unused"] == []

    def test_partial_coverage(self) -> None:
        t = _trace(calls=[_call("a"), _call("b"), _call("c")])
        fix = _fixture([("a", {}), ("b", {}), ("c", {}), ("d", {}), ("e", {})])
        r = tool_coverage().compute(t, fixtures=[fix])
        assert r.score == pytest.approx(3 / 5)
        assert sorted(r.details["unused"]) == ["d", "e"]

    def test_no_fixtures(self) -> None:
        t = _trace(calls=[_call("a")])
        r = tool_coverage().compute(t, fixtures=None)
        assert r.score == pytest.approx(1.0)
        assert "note" in r.details

    def test_no_tools_in_fixture(self) -> None:
        t = _trace(calls=[_call("a")])
        fix = _fixture([])
        r = tool_coverage().compute(t, fixtures=[fix])
        assert r.score == pytest.approx(1.0)

    def test_empty_trace_zero_coverage(self) -> None:
        t = _trace(calls=[])
        fix = _fixture([("a", {}), ("b", {})])
        r = tool_coverage().compute(t, fixtures=[fix])
        assert r.score == pytest.approx(0.0)
        assert sorted(r.details["unused"]) == ["a", "b"]

    def test_name_and_label(self) -> None:
        r = tool_coverage().compute(_trace())
        assert r.name == "tool_coverage"
        assert r.label == "Tool Coverage"


# ---------------------------------------------------------------------------
# TestComputeAll
# ---------------------------------------------------------------------------


class TestComputeAll:
    def test_runs_all_six_metrics(self) -> None:
        t = _trace(calls=[_call("a"), _call("b")])
        results = compute_all(t)
        assert len(results) == 6

    def test_all_results_are_metric_result(self) -> None:
        t = _trace()
        for r in compute_all(t):
            assert isinstance(r, MetricResult)

    def test_scores_in_range(self) -> None:
        t = _trace(calls=[_call("a"), _call("a"), _call("b", error="boom"), _call("c")])
        for r in compute_all(t):
            assert 0.0 <= r.score <= 1.0

    def test_registry_has_all_six(self) -> None:
        expected = {
            "tool_efficiency",
            "redundancy",
            "error_recovery_rate",
            "trajectory_similarity",
            "schema_compliance",
            "tool_coverage",
        }
        assert expected == set(METRICS.keys())

    def test_kwargs_forwarded(self) -> None:
        t = _trace(calls=[_call("a")])
        ref = _trace(calls=[_call("b")])
        results = compute_all(t, reference=ref)
        sim = next(r for r in results if r.name == "trajectory_similarity")
        # With a reference the score should reflect actual distance, not the 1.0 default.
        assert sim.score < 1.0


# ---------------------------------------------------------------------------
# TestRegistry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_duplicate_registration_rejected(self) -> None:
        from mcptest.metrics.base import register_metric

        class _Dup:
            name = "tool_efficiency"
            label = "Dup"

        with pytest.raises(ValueError, match="already registered"):
            register_metric(_Dup)

    def test_missing_name_rejected(self) -> None:
        from mcptest.metrics.base import register_metric

        class _NoName:
            pass

        with pytest.raises(TypeError, match="name"):
            register_metric(_NoName)


# ---------------------------------------------------------------------------
# TestMetricsCLI
# ---------------------------------------------------------------------------


def _write_trace(tmp_path: Path, calls: list[RecordedCall] | None = None) -> Path:
    trace = Trace(
        input="test",
        output="done",
        tool_calls=calls or [],
        duration_s=0.1,
    )
    path = tmp_path / "trace.json"
    trace.save(path)
    return path


def _write_fixture_yaml(tmp_path: Path, tools: list[str]) -> Path:
    lines = [
        "server:",
        "  name: test-server",
        "tools:",
    ]
    for name in tools:
        lines += [
            f"  - name: {name}",
            "    responses:",
            "      - return:",
            "          ok: true",
        ]
    content = "\n".join(lines) + "\n"
    path = tmp_path / "fixture.yaml"
    path.write_text(content, encoding="utf-8")
    return path


class TestMetricsCLI:
    def test_metrics_from_trace_file(self, tmp_path: Path) -> None:
        trace_path = _write_trace(tmp_path, [_call("search"), _call("read")])
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", str(trace_path)])
        assert result.exit_code == 0
        assert "tool_efficiency" in result.output
        assert "Tool Efficiency" in result.output

    def test_metrics_json_output(self, tmp_path: Path) -> None:
        trace_path = _write_trace(tmp_path, [_call("a"), _call("b")])
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", str(trace_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 6
        names = {d["name"] for d in data}
        assert "tool_efficiency" in names
        assert "schema_compliance" in names
        for d in data:
            assert 0.0 <= d["score"] <= 1.0

    def test_metrics_with_fixture(self, tmp_path: Path) -> None:
        trace_path = _write_trace(tmp_path, [_call("search")])
        fixture_path = _write_fixture_yaml(tmp_path, ["search", "read"])
        runner = CliRunner()
        result = runner.invoke(
            main, ["metrics", str(trace_path), "--fixture", str(fixture_path), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        coverage = next(d for d in data if d["name"] == "tool_coverage")
        # search used, read not — 1/2 = 0.5
        assert coverage["score"] == pytest.approx(0.5)

    def test_metrics_with_reference(self, tmp_path: Path) -> None:
        trace_path = _write_trace(tmp_path, [_call("a"), _call("b")])
        ref = Trace(input="", output="", tool_calls=[_call("a"), _call("c")])
        ref_path = tmp_path / "ref.json"
        ref.save(ref_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["metrics", str(trace_path), "--reference", str(ref_path), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        sim = next(d for d in data if d["name"] == "trajectory_similarity")
        assert sim["score"] < 1.0

    def test_metrics_missing_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", str(tmp_path / "nonexistent.json")])
        # click should reject missing file before our code runs
        assert result.exit_code != 0

    def test_metrics_table_has_all_metrics(self, tmp_path: Path) -> None:
        trace_path = _write_trace(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", str(trace_path)])
        assert result.exit_code == 0
        for name in [
            "tool_efficiency",
            "redundancy",
            "error_recovery_rate",
            "trajectory_similarity",
            "schema_compliance",
            "tool_coverage",
        ]:
            assert name in result.output

    def test_metrics_score_coloring(self, tmp_path: Path) -> None:
        # A trace with repeated calls (low efficiency) should still render.
        calls = [_call("a")] * 10
        trace_path = _write_trace(tmp_path, calls)
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", str(trace_path)])
        assert result.exit_code == 0
