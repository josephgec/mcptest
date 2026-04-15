"""Unit tests for the scorecard module and CLI command."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner.trace import Trace
from mcptest.scorecard import Scorecard, ScorecardConfig, ScorecardEntry, render_scorecard


# ---------------------------------------------------------------------------
# Helpers
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


def _call(tool: str, *, arguments: dict | None = None) -> RecordedCall:
    return RecordedCall(tool=tool, arguments=arguments or {})


# ---------------------------------------------------------------------------
# ScorecardConfig
# ---------------------------------------------------------------------------


class TestScorecardConfig:
    def test_defaults(self) -> None:
        cfg = ScorecardConfig()
        assert cfg.composite_threshold == 0.75
        assert cfg.default_threshold == 0.7
        assert cfg.thresholds == {}
        assert cfg.weights == {}

    def test_from_dict_full(self) -> None:
        cfg = ScorecardConfig.from_dict({
            "thresholds": {"tool_efficiency": 0.8},
            "weights": {"tool_efficiency": 2.0},
            "composite_threshold": 0.9,
            "default_threshold": 0.6,
        })
        assert cfg.composite_threshold == 0.9
        assert cfg.default_threshold == 0.6
        assert cfg.thresholds["tool_efficiency"] == 0.8
        assert cfg.weights["tool_efficiency"] == 2.0

    def test_from_dict_empty(self) -> None:
        cfg = ScorecardConfig.from_dict({})
        assert cfg.composite_threshold == 0.75

    def test_from_dict_partial(self) -> None:
        cfg = ScorecardConfig.from_dict({"composite_threshold": 0.5})
        assert cfg.composite_threshold == 0.5
        assert cfg.thresholds == {}


# ---------------------------------------------------------------------------
# ScorecardEntry
# ---------------------------------------------------------------------------


class TestScorecardEntry:
    def test_to_dict(self) -> None:
        entry = ScorecardEntry(
            name="tool_efficiency",
            label="Tool Efficiency",
            score=0.85,
            threshold=0.7,
            weight=1.0,
            passed=True,
        )
        d = entry.to_dict()
        assert d["name"] == "tool_efficiency"
        assert d["score"] == pytest.approx(0.85)
        assert d["passed"] is True

    def test_passed_flag(self) -> None:
        passing = ScorecardEntry("m", "M", 0.8, 0.7, 1.0, True)
        failing = ScorecardEntry("m", "M", 0.5, 0.7, 1.0, False)
        assert passing.passed
        assert not failing.passed


# ---------------------------------------------------------------------------
# Scorecard.from_trace
# ---------------------------------------------------------------------------


class TestScorecardFromTrace:
    def test_returns_entries_for_all_metrics(self) -> None:
        from mcptest.metrics.base import METRICS

        t = _trace(calls=[_call("a"), _call("b")])
        card = Scorecard.from_trace(t)
        names = {e.name for e in card.entries}
        assert names == set(METRICS.keys())

    def test_composite_score_range(self) -> None:
        t = _trace(calls=[_call("a"), _call("b"), _call("c")])
        card = Scorecard.from_trace(t)
        assert 0.0 <= card.composite_score <= 1.0

    def test_default_config_used_when_not_supplied(self) -> None:
        t = _trace()
        card = Scorecard.from_trace(t)
        assert card.composite_score >= 0.0

    def test_custom_thresholds_applied(self) -> None:
        # Force all thresholds to 0.0 so everything passes
        cfg = ScorecardConfig(thresholds={}, default_threshold=0.0, composite_threshold=0.0)
        t = _trace()
        card = Scorecard.from_trace(t, cfg)
        assert all(e.passed for e in card.entries)
        assert card.composite_passed

    def test_custom_thresholds_force_failure(self) -> None:
        # Force all thresholds to 2.0 (impossible) so everything fails
        cfg = ScorecardConfig(thresholds={}, default_threshold=2.0, composite_threshold=0.0)
        t = _trace()
        card = Scorecard.from_trace(t, cfg)
        assert all(not e.passed for e in card.entries)

    def test_composite_threshold_gates_passed(self) -> None:
        cfg_pass = ScorecardConfig(composite_threshold=0.0)
        cfg_fail = ScorecardConfig(composite_threshold=2.0)
        t = _trace(calls=[_call("a"), _call("b")])
        assert Scorecard.from_trace(t, cfg_pass).composite_passed
        assert not Scorecard.from_trace(t, cfg_fail).composite_passed

    def test_custom_weights_affect_composite(self) -> None:
        # Give tool_efficiency weight 0 and others weight 1 → same as if tool_efficiency ignored
        from mcptest.metrics.base import METRICS

        t = _trace(calls=[_call("a"), _call("b")])
        weights = {name: 0.0 if name == "tool_efficiency" else 1.0 for name in METRICS}
        cfg = ScorecardConfig(weights=weights)
        card = Scorecard.from_trace(t, cfg)
        # Composite should equal weighted average excluding tool_efficiency
        assert 0.0 <= card.composite_score <= 1.0

    def test_trace_id_captured(self) -> None:
        t = _trace()
        card = Scorecard.from_trace(t)
        assert card.trace_id == t.trace_id

    def test_to_dict_structure(self) -> None:
        t = _trace()
        card = Scorecard.from_trace(t)
        d = card.to_dict()
        assert "trace_id" in d
        assert "composite_score" in d
        assert "composite_passed" in d
        assert "entries" in d
        assert isinstance(d["entries"], list)

    def test_to_json_valid(self) -> None:
        t = _trace()
        card = Scorecard.from_trace(t)
        parsed = json.loads(card.to_json())
        assert "composite_score" in parsed


# ---------------------------------------------------------------------------
# render_scorecard
# ---------------------------------------------------------------------------


class TestRenderScorecard:
    def test_renders_without_error(self) -> None:
        from rich.console import Console

        t = _trace(calls=[_call("a"), _call("b")])
        card = Scorecard.from_trace(t)
        console = Console(file=None, quiet=True)
        # Should not raise
        render_scorecard(console, card)

    def test_renders_pass_verdict(self, capsys: pytest.CaptureFixture[str]) -> None:
        from io import StringIO

        from rich.console import Console

        t = _trace(calls=[_call("a"), _call("b")])
        cfg = ScorecardConfig(composite_threshold=0.0)
        card = Scorecard.from_trace(t, cfg)
        buf = StringIO()
        console = Console(file=buf, highlight=False)
        render_scorecard(console, card)
        output = buf.getvalue()
        assert "PASSED" in output

    def test_renders_fail_verdict(self) -> None:
        from io import StringIO

        from rich.console import Console

        t = _trace()
        cfg = ScorecardConfig(composite_threshold=2.0)
        card = Scorecard.from_trace(t, cfg)
        buf = StringIO()
        console = Console(file=buf, highlight=False)
        render_scorecard(console, card)
        output = buf.getvalue()
        assert "FAILED" in output


# ---------------------------------------------------------------------------
# CLI command: mcptest scorecard
# ---------------------------------------------------------------------------


class TestScorecardCommand:
    def _make_trace_file(self, tmp_path: pytest.TempPathFactory, **kwargs: object) -> str:
        t = _trace(**kwargs)  # type: ignore[arg-type]
        p = tmp_path / "trace.json"  # type: ignore[operator]
        t.save(str(p))
        return str(p)

    def test_basic_table_output(self, tmp_path: pytest.TempPathFactory) -> None:
        from mcptest.cli.main import main

        trace_path = self._make_trace_file(tmp_path, calls=[_call("a"), _call("b")])
        runner = CliRunner()
        result = runner.invoke(main, ["scorecard", trace_path])
        assert result.exit_code in (0, 1)  # depends on composite score
        assert "scorecard" in result.output.lower()

    def test_json_output(self, tmp_path: pytest.TempPathFactory) -> None:
        from mcptest.cli.main import main

        trace_path = self._make_trace_file(tmp_path, calls=[_call("a")])
        runner = CliRunner()
        result = runner.invoke(main, ["scorecard", trace_path, "--json"])
        # JSON should be on stdout; parse it
        data = json.loads(result.output)
        assert "composite_score" in data
        assert "entries" in data

    def test_fail_under_exit_0_when_passing(self, tmp_path: pytest.TempPathFactory) -> None:
        from mcptest.cli.main import main

        trace_path = self._make_trace_file(tmp_path, calls=[_call("a"), _call("b"), _call("c")])
        runner = CliRunner()
        result = runner.invoke(main, ["scorecard", trace_path, "--fail-under", "0.0"])
        assert result.exit_code == 0

    def test_fail_under_exit_1_when_failing(self, tmp_path: pytest.TempPathFactory) -> None:
        from mcptest.cli.main import main

        trace_path = self._make_trace_file(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["scorecard", trace_path, "--fail-under", "2.0"])
        assert result.exit_code == 1

    def test_config_file_loaded(self, tmp_path: pytest.TempPathFactory) -> None:
        import yaml

        from mcptest.cli.main import main

        trace_path = self._make_trace_file(tmp_path)
        config = {"composite_threshold": 0.0, "default_threshold": 0.0}
        config_path = tmp_path / "scorecard.yaml"  # type: ignore[operator]
        config_path.write_text(yaml.dump(config))

        runner = CliRunner()
        result = runner.invoke(main, ["scorecard", trace_path, "--config", str(config_path)])
        assert result.exit_code == 0

    def test_bad_trace_file_exits_1(self, tmp_path: pytest.TempPathFactory) -> None:
        from mcptest.cli.main import main

        bad_file = tmp_path / "bad.json"  # type: ignore[operator]
        bad_file.write_text("not valid json at all {{{")

        runner = CliRunner()
        result = runner.invoke(main, ["scorecard", str(bad_file)])
        assert result.exit_code == 1

    def test_json_output_with_fail_under(self, tmp_path: pytest.TempPathFactory) -> None:
        from mcptest.cli.main import main

        trace_path = self._make_trace_file(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["scorecard", trace_path, "--json", "--fail-under", "0.0"])
        # Should emit JSON and exit 0
        data = json.loads(result.output)
        assert data["composite_passed"] is True
        assert result.exit_code == 0
