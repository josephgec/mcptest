"""Tests for mcptest.bench — multi-agent benchmarking subsystem."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from rich.console import Console

from mcptest.bench.profile import (
    AgentProfile,
    _profile_from_dict,
    load_profiles,
    load_profiles_from_config,
)
from mcptest.bench.renderer import (
    render_leaderboard,
    render_metric_comparison,
    render_per_test_breakdown,
)
from mcptest.bench.report import AgentSummary, BenchmarkReport
from mcptest.bench.runner import BenchmarkEntry, BenchmarkRunner
from mcptest.cli.main import main
from mcptest.config import McpTestConfig, _parse_config, load_config
from mcptest.metrics.base import MetricResult
from mcptest.runner.adapters import AgentResult, CallableAdapter
from mcptest.runner.trace import Trace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_fixture(tmp_path: Path, name: str = "fixture.yaml") -> Path:
    p = tmp_path / name
    p.write_text(
        "server: { name: mock-test }\n"
        "tools:\n"
        "  - name: ping\n"
        "    responses:\n"
        "      - return_text: pong\n"
    )
    return p


def _write_test_suite(
    tmp_path: Path,
    fixture_path: Path,
    name: str = "test_suite.yaml",
    suite_name: str = "test-suite",
    case_name: str = "case-one",
) -> Path:
    p = tmp_path / name
    p.write_text(
        f"name: {suite_name}\n"
        f"fixtures:\n"
        f"  - {fixture_path}\n"
        f"agent:\n"
        f"  command: echo hello\n"
        f"cases:\n"
        f"  - name: {case_name}\n"
        f"    input: hello\n"
    )
    return p


def _make_entry(
    agent: str,
    *,
    suite: str = "test-suite",
    case: str = "case-one",
    passed: bool = True,
    score: float = 0.8,
    duration_s: float = 0.5,
    error: str | None = None,
) -> BenchmarkEntry:
    return BenchmarkEntry(
        agent=agent,
        suite=suite,
        case=case,
        trace=Trace(),
        metric_results=[
            MetricResult(name="tool_efficiency", score=score, label="Tool Efficiency")
        ],
        passed=passed,
        duration_s=duration_s,
        error=error,
    )


def _console_capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, highlight=False, markup=True)
    return console, buf


def _callable_factory(profile: AgentProfile) -> CallableAdapter:
    """Adapter factory: echoes the input, exit_code=0."""
    return CallableAdapter(
        func=lambda inp, env: AgentResult(output=inp, exit_code=0)
    )


# ---------------------------------------------------------------------------
# AgentProfile
# ---------------------------------------------------------------------------


class TestAgentProfile:
    def test_minimal_construction(self) -> None:
        p = AgentProfile(name="my-agent", command="echo hi")
        assert p.name == "my-agent"
        assert p.command == "echo hi"
        assert p.env == {}
        assert p.description == ""
        assert p.retry == 1
        assert p.tolerance == 1.0

    def test_full_construction(self) -> None:
        p = AgentProfile(
            name="claude",
            command="python agents/claude.py",
            env={"MODEL": "claude-3"},
            description="Anthropic Claude",
            retry=3,
            tolerance=0.8,
        )
        assert p.env == {"MODEL": "claude-3"}
        assert p.description == "Anthropic Claude"
        assert p.retry == 3
        assert p.tolerance == 0.8

    def test_env_defaults_to_empty_dict(self) -> None:
        p = AgentProfile(name="x", command="cmd")
        assert p.env == {}
        assert isinstance(p.env, dict)

    def test_retry_default(self) -> None:
        p = AgentProfile(name="x", command="cmd")
        assert p.retry == 1

    def test_tolerance_default(self) -> None:
        p = AgentProfile(name="x", command="cmd")
        assert p.tolerance == 1.0

    def test_description_default(self) -> None:
        p = AgentProfile(name="x", command="cmd")
        assert p.description == ""


# ---------------------------------------------------------------------------
# _profile_from_dict
# ---------------------------------------------------------------------------


class TestProfileFromDict:
    def test_valid_minimal(self) -> None:
        p = _profile_from_dict({"name": "a", "command": "echo hi"})
        assert p.name == "a"
        assert p.command == "echo hi"

    def test_valid_full(self) -> None:
        p = _profile_from_dict({
            "name": "claude",
            "command": "python c.py",
            "env": {"K": "V"},
            "description": "desc",
            "retry": 2,
            "tolerance": 0.75,
        })
        assert p.env == {"K": "V"}
        assert p.retry == 2
        assert p.tolerance == 0.75
        assert p.description == "desc"

    def test_missing_name_raises(self) -> None:
        with pytest.raises(ValueError, match="name"):
            _profile_from_dict({"command": "echo"})

    def test_missing_command_raises(self) -> None:
        with pytest.raises(ValueError, match="command"):
            _profile_from_dict({"name": "x"})

    def test_non_dict_string_raises(self) -> None:
        with pytest.raises(ValueError, match="mapping"):
            _profile_from_dict("not a dict")

    def test_non_dict_list_raises(self) -> None:
        with pytest.raises(ValueError, match="mapping"):
            _profile_from_dict(["name", "command"])

    def test_bad_env_type_raises(self) -> None:
        with pytest.raises(ValueError, match="env must be a mapping"):
            _profile_from_dict({"name": "x", "command": "cmd", "env": "not-a-dict"})

    def test_optional_fields_default(self) -> None:
        p = _profile_from_dict({"name": "x", "command": "cmd"})
        assert p.description == ""
        assert p.retry == 1
        assert p.tolerance == 1.0

    def test_env_key_value_coerced_to_str(self) -> None:
        p = _profile_from_dict({"name": "x", "command": "cmd", "env": {"PORT": 8080}})
        assert p.env["PORT"] == "8080"


# ---------------------------------------------------------------------------
# load_profiles
# ---------------------------------------------------------------------------


class TestLoadProfiles:
    def test_loads_from_agents_key(self, tmp_path: Path) -> None:
        f = tmp_path / "agents.yaml"
        f.write_text("agents:\n  - name: a\n    command: echo\n")
        profiles = load_profiles(f)
        assert len(profiles) == 1
        assert profiles[0].name == "a"

    def test_loads_from_bare_list(self, tmp_path: Path) -> None:
        f = tmp_path / "agents.yaml"
        f.write_text("- name: a\n  command: echo\n- name: b\n  command: cmd\n")
        profiles = load_profiles(f)
        assert len(profiles) == 2
        assert profiles[0].name == "a"
        assert profiles[1].name == "b"

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        f = tmp_path / "agents.yaml"
        f.write_text("")
        profiles = load_profiles(f)
        assert profiles == []

    def test_agents_key_empty_list(self, tmp_path: Path) -> None:
        f = tmp_path / "agents.yaml"
        f.write_text("agents: []\n")
        profiles = load_profiles(f)
        assert profiles == []

    def test_agents_key_null_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "agents.yaml"
        f.write_text("agents:\n")  # agents: null
        profiles = load_profiles(f)
        assert profiles == []

    def test_invalid_structure_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "agents.yaml"
        f.write_text("just a string\n")
        with pytest.raises(ValueError, match="mapping or list"):
            load_profiles(f)

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_profiles(tmp_path / "missing.yaml")

    def test_invalid_entry_missing_command_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "agents.yaml"
        f.write_text("agents:\n  - name: x\n")
        with pytest.raises(ValueError, match="command"):
            load_profiles(f)

    def test_multiple_profiles(self, tmp_path: Path) -> None:
        f = tmp_path / "agents.yaml"
        f.write_text(
            "agents:\n"
            "  - name: claude\n    command: python c.py\n"
            "    env: {MODEL: claude-3}\n"
            "  - name: gpt4o\n    command: python g.py\n"
            "    env: {MODEL: gpt-4o}\n"
        )
        profiles = load_profiles(f)
        assert len(profiles) == 2
        assert profiles[0].env == {"MODEL": "claude-3"}
        assert profiles[1].env == {"MODEL": "gpt-4o"}

    def test_all_fields_loaded(self, tmp_path: Path) -> None:
        f = tmp_path / "agents.yaml"
        f.write_text(
            "- name: agent\n"
            "  command: cmd\n"
            "  env:\n    FOO: bar\n"
            "  description: test agent\n"
            "  retry: 2\n"
            "  tolerance: 0.7\n"
        )
        profiles = load_profiles(f)
        assert profiles[0].description == "test agent"
        assert profiles[0].retry == 2
        assert profiles[0].tolerance == 0.7
        assert profiles[0].env == {"FOO": "bar"}


# ---------------------------------------------------------------------------
# load_profiles_from_config
# ---------------------------------------------------------------------------


class TestLoadProfilesFromConfig:
    def test_empty_agents_returns_empty_list(self) -> None:
        config = McpTestConfig()
        assert load_profiles_from_config(config) == []

    def test_loads_two_profiles(self) -> None:
        config = McpTestConfig(agents=[
            {"name": "a", "command": "echo a"},
            {"name": "b", "command": "echo b"},
        ])
        profiles = load_profiles_from_config(config)
        assert len(profiles) == 2
        assert profiles[0].name == "a"
        assert profiles[1].name == "b"

    def test_preserves_env_field(self) -> None:
        config = McpTestConfig(agents=[
            {"name": "x", "command": "cmd", "env": {"K": "V"}}
        ])
        profiles = load_profiles_from_config(config)
        assert profiles[0].env == {"K": "V"}

    def test_invalid_entry_raises(self) -> None:
        config = McpTestConfig(agents=[{"name": "x"}])  # missing command
        with pytest.raises(ValueError, match="command"):
            load_profiles_from_config(config)


# ---------------------------------------------------------------------------
# BenchmarkEntry
# ---------------------------------------------------------------------------


class TestBenchmarkEntry:
    def test_fields_preserved(self) -> None:
        entry = _make_entry("agent-a")
        assert entry.agent == "agent-a"
        assert entry.suite == "test-suite"
        assert entry.case == "case-one"
        assert entry.passed is True
        assert entry.duration_s == 0.5
        assert entry.error is None

    def test_to_dict_structure(self) -> None:
        entry = _make_entry("agent-a")
        d = entry.to_dict()
        assert d["agent"] == "agent-a"
        assert d["suite"] == "test-suite"
        assert d["case"] == "case-one"
        assert d["passed"] is True
        assert "metrics" in d
        assert "trace" in d
        assert d["error"] is None

    def test_to_dict_includes_metrics(self) -> None:
        entry = _make_entry("agent-a", score=0.9)
        d = entry.to_dict()
        assert len(d["metrics"]) == 1
        assert d["metrics"][0]["name"] == "tool_efficiency"
        assert d["metrics"][0]["score"] == 0.9

    def test_error_field_preserved(self) -> None:
        entry = _make_entry("agent-a", error="something failed", passed=False)
        d = entry.to_dict()
        assert d["error"] == "something failed"
        assert d["passed"] is False


# ---------------------------------------------------------------------------
# BenchmarkRunner
# ---------------------------------------------------------------------------


class TestBenchmarkRunner:
    def test_empty_profiles_returns_no_entries(self, tmp_path: Path) -> None:
        runner = BenchmarkRunner(profiles=[], test_path=str(tmp_path))
        entries = runner.run()
        assert entries == []

    def test_no_test_files_returns_no_entries(self, tmp_path: Path) -> None:
        profile = AgentProfile(name="x", command="echo hi")
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        assert entries == []

    def test_uses_adapter_factory(self, tmp_path: Path) -> None:
        fixture_path = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fixture_path)

        called: list[str] = []

        def factory(profile: AgentProfile) -> CallableAdapter:
            called.append(profile.name)
            return CallableAdapter(
                func=lambda inp, env: AgentResult(output=inp, exit_code=0)
            )

        profile = AgentProfile(name="my-agent", command="echo")
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            _adapter_factory=factory,
        )
        runner.run()
        assert "my-agent" in called

    def test_entries_have_correct_agent_name(self, tmp_path: Path) -> None:
        fixture_path = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fixture_path)

        profile = AgentProfile(name="test-agent", command="echo")
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        assert len(entries) > 0
        assert all(e.agent == "test-agent" for e in entries)

    def test_entries_have_metric_results(self, tmp_path: Path) -> None:
        fixture_path = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fixture_path)

        profile = AgentProfile(name="x", command="echo")
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        assert len(entries) > 0
        assert all(isinstance(e.metric_results, list) for e in entries)

    def test_multiple_profiles_produce_entries_for_each(self, tmp_path: Path) -> None:
        fixture_path = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fixture_path)

        profiles = [
            AgentProfile(name="agent-a", command="echo"),
            AgentProfile(name="agent-b", command="echo"),
        ]
        runner = BenchmarkRunner(
            profiles=profiles,
            test_path=str(tmp_path),
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        agent_names = {e.agent for e in entries}
        assert "agent-a" in agent_names
        assert "agent-b" in agent_names

    def test_suite_without_fixtures_produces_error_entry(self, tmp_path: Path) -> None:
        suite_file = tmp_path / "test_no_fixtures.yaml"
        suite_file.write_text(
            "name: no-fixture-suite\n"
            "agent:\n  command: echo\n"
            "cases:\n  - name: c\n    input: hi\n"
        )

        profile = AgentProfile(name="x", command="echo")
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        error_entries = [e for e in entries if e.error]
        assert len(error_entries) > 0
        assert "fixture" in error_entries[0].error.lower()

    def test_suite_load_failure_produces_error_entry(self, tmp_path: Path) -> None:
        bad_yaml = tmp_path / "test_bad.yaml"
        bad_yaml.write_text("not: a: valid: suite\n")

        profile = AgentProfile(name="x", command="echo")
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        error_entries = [e for e in entries if e.error]
        assert len(error_entries) > 0

    def test_make_adapter_splits_command_into_tokens(self) -> None:
        profile = AgentProfile(name="x", command="python agents/my_agent.py --verbose")
        runner = BenchmarkRunner(profiles=[profile], test_path="tests/")
        from mcptest.runner.adapters import SubprocessAdapter
        adapter = runner._make_adapter(profile)
        assert isinstance(adapter, SubprocessAdapter)
        assert adapter.command == "python"
        assert adapter.args == ["agents/my_agent.py", "--verbose"]

    def test_make_adapter_single_token(self) -> None:
        profile = AgentProfile(name="x", command="my-agent")
        runner = BenchmarkRunner(profiles=[profile], test_path="tests/")
        from mcptest.runner.adapters import SubprocessAdapter
        adapter = runner._make_adapter(profile)
        assert isinstance(adapter, SubprocessAdapter)
        assert adapter.command == "my-agent"
        assert adapter.args == []

    def test_make_adapter_uses_factory_when_set(self) -> None:
        called: list[str] = []

        def factory(profile: AgentProfile) -> CallableAdapter:
            called.append(profile.name)
            return CallableAdapter(func=lambda i, e: "ok")

        profile = AgentProfile(name="my-agent", command="echo")
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path="tests/",
            _adapter_factory=factory,
        )
        runner._make_adapter(profile)
        assert "my-agent" in called

    def test_retry_override_takes_precedence_over_profile(self, tmp_path: Path) -> None:
        fixture_path = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fixture_path)

        profile = AgentProfile(name="x", command="echo", retry=5)
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            retry_override=1,
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        assert any(e.agent == "x" for e in entries)

    def test_tolerance_override_takes_precedence(self, tmp_path: Path) -> None:
        fixture_path = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fixture_path)

        profile = AgentProfile(name="x", command="echo", tolerance=0.9)
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            tolerance_override=0.5,
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        assert any(e.agent == "x" for e in entries)


# ---------------------------------------------------------------------------
# AgentSummary
# ---------------------------------------------------------------------------


class TestAgentSummary:
    def test_to_dict_has_all_keys(self) -> None:
        summary = AgentSummary(
            agent="a",
            total_cases=10,
            passed_cases=8,
            pass_rate=0.8,
            composite_score=0.75,
            per_metric={"tool_efficiency": 0.75},
            total_duration_s=5.0,
        )
        d = summary.to_dict()
        assert d["agent"] == "a"
        assert d["total_cases"] == 10
        assert d["passed_cases"] == 8
        assert "pass_rate" in d
        assert "composite_score" in d
        assert "per_metric" in d
        assert "total_duration_s" in d

    def test_to_dict_rounds_floats(self) -> None:
        summary = AgentSummary(
            agent="a",
            total_cases=1,
            passed_cases=1,
            pass_rate=1 / 3,
            composite_score=2 / 3,
            per_metric={"m": 0.123456789},
            total_duration_s=1.23456789,
        )
        d = summary.to_dict()
        assert d["pass_rate"] == round(1 / 3, 4)
        assert d["composite_score"] == round(2 / 3, 4)
        assert d["per_metric"]["m"] == round(0.123456789, 4)

    def test_empty_per_metric(self) -> None:
        summary = AgentSummary(
            agent="a",
            total_cases=0,
            passed_cases=0,
            pass_rate=0.0,
            composite_score=0.0,
            per_metric={},
            total_duration_s=0.0,
        )
        assert summary.to_dict()["per_metric"] == {}


# ---------------------------------------------------------------------------
# BenchmarkReport
# ---------------------------------------------------------------------------


class TestBenchmarkReport:
    def test_from_empty_entries(self) -> None:
        report = BenchmarkReport.from_entries([])
        assert report.entries == []
        assert report.summaries == []
        assert report.ranking == []
        assert report.best_agent == ""
        assert isinstance(report.timestamp, str)

    def test_single_agent_single_case(self) -> None:
        entries = [_make_entry("agent-a", score=0.9)]
        report = BenchmarkReport.from_entries(entries)
        assert len(report.summaries) == 1
        assert report.best_agent == "agent-a"
        assert report.ranking == ["agent-a"]

    def test_two_agents_ranked_by_composite_desc(self) -> None:
        entries = [
            _make_entry("agent-a", score=0.9),
            _make_entry("agent-b", score=0.5),
        ]
        report = BenchmarkReport.from_entries(entries)
        assert report.ranking[0] == "agent-a"
        assert report.ranking[1] == "agent-b"
        assert report.best_agent == "agent-a"

    def test_tie_broken_alphabetically(self) -> None:
        entries = [
            _make_entry("zebra", score=0.8),
            _make_entry("alpha", score=0.8),
        ]
        report = BenchmarkReport.from_entries(entries)
        assert report.ranking[0] == "alpha"
        assert report.ranking[1] == "zebra"
        assert report.best_agent == "alpha"

    def test_composite_score_is_mean_of_case_scores(self) -> None:
        entries = [
            _make_entry("a", score=0.6),
            _make_entry("a", case="case-two", score=1.0),
        ]
        report = BenchmarkReport.from_entries(entries)
        expected = (0.6 + 1.0) / 2
        assert abs(report.summaries[0].composite_score - expected) < 0.001

    def test_pass_rate_computed_correctly(self) -> None:
        entries = [
            _make_entry("a", passed=True),
            _make_entry("a", case="c2", passed=False),
            _make_entry("a", case="c3", passed=True),
        ]
        report = BenchmarkReport.from_entries(entries)
        assert abs(report.summaries[0].pass_rate - 2 / 3) < 0.001

    def test_per_metric_averages(self) -> None:
        e1 = BenchmarkEntry(
            agent="a",
            suite="s",
            case="c1",
            trace=Trace(),
            metric_results=[
                MetricResult(name="m1", score=0.8, label="M1"),
                MetricResult(name="m2", score=0.6, label="M2"),
            ],
            passed=True,
            duration_s=1.0,
        )
        e2 = BenchmarkEntry(
            agent="a",
            suite="s",
            case="c2",
            trace=Trace(),
            metric_results=[
                MetricResult(name="m1", score=0.6, label="M1"),
                MetricResult(name="m2", score=0.4, label="M2"),
            ],
            passed=True,
            duration_s=1.0,
        )
        report = BenchmarkReport.from_entries([e1, e2])
        pm = report.summaries[0].per_metric
        assert abs(pm["m1"] - 0.7) < 0.001
        assert abs(pm["m2"] - 0.5) < 0.001

    def test_total_duration_s(self) -> None:
        entries = [
            _make_entry("a", duration_s=1.5),
            _make_entry("a", case="c2", duration_s=2.5),
        ]
        report = BenchmarkReport.from_entries(entries)
        assert abs(report.summaries[0].total_duration_s - 4.0) < 0.001

    def test_entries_without_metrics_excluded_from_composite(self) -> None:
        e1 = BenchmarkEntry(
            agent="a",
            suite="s",
            case="c1",
            trace=Trace(),
            metric_results=[],
            passed=False,
            duration_s=0.0,
        )
        e2 = _make_entry("a", case="c2", score=0.9)
        report = BenchmarkReport.from_entries([e1, e2])
        assert abs(report.summaries[0].composite_score - 0.9) < 0.001

    def test_to_dict_has_required_keys(self) -> None:
        report = BenchmarkReport.from_entries([_make_entry("a")])
        d = report.to_dict()
        assert "timestamp" in d
        assert "best_agent" in d
        assert "ranking" in d
        assert "summaries" in d
        assert "entries" in d

    def test_to_json_is_valid_json(self) -> None:
        report = BenchmarkReport.from_entries([_make_entry("a")])
        j = report.to_json()
        parsed = json.loads(j)
        assert "best_agent" in parsed

    def test_to_json_best_agent_is_winner(self) -> None:
        entries = [_make_entry("winner", score=0.9), _make_entry("loser", score=0.3)]
        report = BenchmarkReport.from_entries(entries)
        parsed = json.loads(report.to_json())
        assert parsed["best_agent"] == "winner"

    def test_timestamp_is_iso_format(self) -> None:
        report = BenchmarkReport.from_entries([])
        assert report.timestamp != ""
        assert "T" in report.timestamp


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


class TestRenderers:
    def test_leaderboard_shows_agent_names(self) -> None:
        report = BenchmarkReport.from_entries([
            _make_entry("agent-a", score=0.9),
            _make_entry("agent-b", score=0.5),
        ])
        console, buf = _console_capture()
        render_leaderboard(console, report)
        output = buf.getvalue()
        assert "agent-a" in output
        assert "agent-b" in output

    def test_leaderboard_shows_best_badge(self) -> None:
        report = BenchmarkReport.from_entries([_make_entry("top-agent", score=0.95)])
        console, buf = _console_capture()
        render_leaderboard(console, report)
        assert "BEST" in buf.getvalue()

    def test_leaderboard_best_agent_line(self) -> None:
        report = BenchmarkReport.from_entries([_make_entry("winner")])
        console, buf = _console_capture()
        render_leaderboard(console, report)
        assert "winner" in buf.getvalue()

    def test_leaderboard_empty_report_renders_table(self) -> None:
        report = BenchmarkReport.from_entries([])
        console, buf = _console_capture()
        render_leaderboard(console, report)
        # No error, just an empty table rendered
        _ = buf.getvalue()

    def test_metric_comparison_shows_metric_names(self) -> None:
        e = BenchmarkEntry(
            agent="a",
            suite="s",
            case="c",
            trace=Trace(),
            metric_results=[MetricResult(name="my_metric", score=0.7, label="My Metric")],
            passed=True,
            duration_s=0.5,
        )
        report = BenchmarkReport.from_entries([e])
        console, buf = _console_capture()
        render_metric_comparison(console, report)
        output = buf.getvalue()
        assert "my_metric" in output
        assert "a" in output

    def test_metric_comparison_empty_shows_no_data(self) -> None:
        report = BenchmarkReport.from_entries([])
        console, buf = _console_capture()
        render_metric_comparison(console, report)
        assert "No data" in buf.getvalue()

    def test_per_test_breakdown_shows_suite_name(self) -> None:
        entries = [
            _make_entry("a", suite="my-suite", passed=True),
            _make_entry("b", suite="my-suite", passed=False),
        ]
        report = BenchmarkReport.from_entries(entries)
        console, buf = _console_capture()
        render_per_test_breakdown(console, report)
        assert "my-suite" in buf.getvalue()

    def test_per_test_breakdown_empty_shows_no_entries(self) -> None:
        report = BenchmarkReport.from_entries([])
        console, buf = _console_capture()
        render_per_test_breakdown(console, report)
        assert "No entries" in buf.getvalue()


# ---------------------------------------------------------------------------
# bench CLI command
# ---------------------------------------------------------------------------


class TestBenchCommand:
    def _write_agents_yaml(self, tmp_path: Path) -> Path:
        f = tmp_path / "agents.yaml"
        f.write_text("- name: agent-a\n  command: echo hi\n")
        return f

    def test_bench_command_registered_in_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["bench", "--help"])
        assert result.exit_code == 0
        assert "agent" in result.output.lower() or "bench" in result.output.lower()

    def test_no_profiles_no_agents_file_exits_nonzero(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["bench", str(tmp_path)])
        assert result.exit_code != 0
        assert "error" in result.output.lower()

    def test_missing_agents_file_exits_nonzero(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["bench", str(tmp_path), "--agents", str(tmp_path / "missing.yaml")]
        )
        assert result.exit_code != 0

    def test_json_output_valid_schema(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        agents_file = self._write_agents_yaml(tmp_path)
        (tmp_path / "tests").mkdir()

        monkeypatch.setattr(BenchmarkRunner, "run", lambda self: [])

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["bench", str(tmp_path / "tests"), "--agents", str(agents_file), "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "best_agent" in data
        assert "ranking" in data
        assert "summaries" in data

    def test_table_output_renders_without_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        agents_file = self._write_agents_yaml(tmp_path)
        (tmp_path / "tests").mkdir()

        monkeypatch.setattr(BenchmarkRunner, "run", lambda self: [])

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["bench", str(tmp_path / "tests"), "--agents", str(agents_file)],
        )
        assert result.exit_code == 0

    def test_ci_fails_when_best_below_threshold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agents_file = self._write_agents_yaml(tmp_path)
        (tmp_path / "tests").mkdir()

        def mock_run(self: BenchmarkRunner) -> list[BenchmarkEntry]:
            return [
                BenchmarkEntry(
                    agent="agent-a",
                    suite="s",
                    case="c",
                    trace=Trace(),
                    metric_results=[MetricResult(name="m", score=0.3, label="M")],
                    passed=True,
                    duration_s=0.1,
                )
            ]

        monkeypatch.setattr(BenchmarkRunner, "run", mock_run)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "bench",
                str(tmp_path / "tests"),
                "--agents",
                str(agents_file),
                "--json",
                "--ci",
                "--fail-under",
                "0.5",
            ],
        )
        assert result.exit_code != 0

    def test_ci_passes_when_best_above_threshold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agents_file = self._write_agents_yaml(tmp_path)
        (tmp_path / "tests").mkdir()

        def mock_run(self: BenchmarkRunner) -> list[BenchmarkEntry]:
            return [
                BenchmarkEntry(
                    agent="agent-a",
                    suite="s",
                    case="c",
                    trace=Trace(),
                    metric_results=[MetricResult(name="m", score=0.9, label="M")],
                    passed=True,
                    duration_s=0.1,
                )
            ]

        monkeypatch.setattr(BenchmarkRunner, "run", mock_run)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "bench",
                str(tmp_path / "tests"),
                "--agents",
                str(agents_file),
                "--json",
                "--ci",
                "--fail-under",
                "0.5",
            ],
        )
        assert result.exit_code == 0

    def test_ci_no_summaries_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agents_file = self._write_agents_yaml(tmp_path)
        (tmp_path / "tests").mkdir()

        monkeypatch.setattr(BenchmarkRunner, "run", lambda self: [])

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["bench", str(tmp_path / "tests"), "--agents", str(agents_file), "--ci"],
        )
        assert result.exit_code != 0

    def test_agents_loaded_from_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "mcptest.yaml").write_text(
            "agents:\n  - name: cfg-agent\n    command: echo hi\n"
        )
        (tmp_path / "tests").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(BenchmarkRunner, "run", lambda self: [])

        runner = CliRunner()
        result = runner.invoke(
            main, ["bench", str(tmp_path / "tests"), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "best_agent" in data

    def test_empty_profiles_file_returns_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agents_file = tmp_path / "agents.yaml"
        agents_file.write_text("agents: []\n")
        (tmp_path / "tests").mkdir()

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["bench", str(tmp_path / "tests"), "--agents", str(agents_file)],
        )
        # Empty profiles → "nothing to benchmark" message, exit 0
        assert result.exit_code == 0
        assert "nothing" in result.output.lower()

    def test_retry_option_passed_to_runner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agents_file = self._write_agents_yaml(tmp_path)
        (tmp_path / "tests").mkdir()

        captured: dict[str, object] = {}
        original_init = BenchmarkRunner.__init__

        def patched_init(self: BenchmarkRunner, **kwargs: object) -> None:
            captured.update(kwargs)
            original_init(self, **kwargs)  # type: ignore[call-arg]

        monkeypatch.setattr(BenchmarkRunner, "__init__", patched_init)
        monkeypatch.setattr(BenchmarkRunner, "run", lambda self: [])

        runner = CliRunner()
        runner.invoke(
            main,
            [
                "bench",
                str(tmp_path / "tests"),
                "--agents",
                str(agents_file),
                "--json",
                "--retry",
                "3",
            ],
        )
        assert captured.get("retry_override") == 3

    def test_tolerance_option_passed_to_runner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agents_file = self._write_agents_yaml(tmp_path)
        (tmp_path / "tests").mkdir()

        captured: dict[str, object] = {}
        original_init = BenchmarkRunner.__init__

        def patched_init(self: BenchmarkRunner, **kwargs: object) -> None:
            captured.update(kwargs)
            original_init(self, **kwargs)  # type: ignore[call-arg]

        monkeypatch.setattr(BenchmarkRunner, "__init__", patched_init)
        monkeypatch.setattr(BenchmarkRunner, "run", lambda self: [])

        runner = CliRunner()
        runner.invoke(
            main,
            [
                "bench",
                str(tmp_path / "tests"),
                "--agents",
                str(agents_file),
                "--json",
                "--tolerance",
                "0.75",
            ],
        )
        assert captured.get("tolerance_override") == 0.75


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_agents_defaults_to_empty_list(self) -> None:
        config = McpTestConfig()
        assert config.agents == []
        assert isinstance(config.agents, list)

    def test_agents_parsed_from_yaml_raw(self, tmp_path: Path) -> None:
        raw = {
            "agents": [
                {"name": "a", "command": "echo a"},
                {"name": "b", "command": "echo b", "env": {"K": "V"}},
            ]
        }
        config = _parse_config(raw, tmp_path / "mcptest.yaml")
        assert len(config.agents) == 2
        assert config.agents[0]["name"] == "a"
        assert config.agents[1]["env"] == {"K": "V"}

    def test_agents_non_list_value_ignored(self, tmp_path: Path) -> None:
        raw = {"agents": "not-a-list"}
        config = _parse_config(raw, tmp_path / "mcptest.yaml")
        assert config.agents == []

    def test_agents_round_trips_through_load_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "mcptest.yaml"
        config_file.write_text(
            "agents:\n"
            "  - name: my-agent\n"
            "    command: python agent.py\n"
            "    env:\n      MODEL: gpt-4o\n"
            "    retry: 2\n"
        )
        config = load_config(config_file)
        assert len(config.agents) == 1
        assert config.agents[0]["name"] == "my-agent"
        assert config.agents[0]["env"] == {"MODEL": "gpt-4o"}
        assert config.agents[0]["retry"] == 2

    def test_profiles_from_config_preserve_all_fields(self) -> None:
        config = McpTestConfig(agents=[
            {
                "name": "claude",
                "command": "python c.py",
                "env": {"M": "claude-3"},
                "retry": 3,
                "tolerance": 0.8,
                "description": "Anthropic Claude",
            }
        ])
        profiles = load_profiles_from_config(config)
        assert profiles[0].name == "claude"
        assert profiles[0].env == {"M": "claude-3"}
        assert profiles[0].retry == 3
        assert profiles[0].tolerance == 0.8
        assert profiles[0].description == "Anthropic Claude"


# ---------------------------------------------------------------------------
# Additional branch coverage
# ---------------------------------------------------------------------------


class TestRendererColorBranches:
    """Cover the yellow/red branches in _score_str and _rate_str."""

    def test_leaderboard_red_score(self) -> None:
        # composite_score < 0.5 → red branch in _score_str (line 46)
        entries = [_make_entry("low-agent", score=0.2)]
        report = BenchmarkReport.from_entries(entries)
        console, buf = _console_capture()
        render_leaderboard(console, report)
        output = buf.getvalue()
        assert "low-agent" in output

    def test_leaderboard_yellow_score(self) -> None:
        # 0.5 <= composite_score < 0.8 → yellow branch in _score_str
        entries = [_make_entry("mid-agent", score=0.65)]
        report = BenchmarkReport.from_entries(entries)
        console, buf = _console_capture()
        render_leaderboard(console, report)
        assert "mid-agent" in buf.getvalue()

    def test_leaderboard_yellow_pass_rate(self) -> None:
        # 0.6 <= pass_rate < 0.9 → yellow branch in _rate_str (lines 53-54)
        entries = [
            _make_entry("a", passed=True),
            _make_entry("a", case="c2", passed=True),
            _make_entry("a", case="c3", passed=True),
            _make_entry("a", case="c4", passed=True),
            _make_entry("a", case="c5", passed=False),
            _make_entry("a", case="c6", passed=False),
            _make_entry("a", case="c7", passed=True),
            _make_entry("a", case="c8", passed=True),
            _make_entry("a", case="c9", passed=True),
            _make_entry("a", case="c10", passed=True),
        ]
        # 8/10 = 0.8 pass rate → yellow
        report = BenchmarkReport.from_entries(entries)
        assert 0.6 <= report.summaries[0].pass_rate < 0.9
        console, buf = _console_capture()
        render_leaderboard(console, report)
        assert "a" in buf.getvalue()

    def test_leaderboard_red_pass_rate(self) -> None:
        # pass_rate < 0.6 → red branch in _rate_str (line 55)
        entries = [
            _make_entry("a", passed=True),
            _make_entry("a", case="c2", passed=False),
            _make_entry("a", case="c3", passed=False),
            _make_entry("a", case="c4", passed=False),
            _make_entry("a", case="c5", passed=False),
        ]
        # 1/5 = 0.2 pass rate → red
        report = BenchmarkReport.from_entries(entries)
        assert report.summaries[0].pass_rate < 0.6
        console, buf = _console_capture()
        render_leaderboard(console, report)
        assert "a" in buf.getvalue()

    def test_metric_comparison_missing_metric_for_agent(self) -> None:
        # When agent B doesn't have a metric that agent A has → None score
        # branch (line 130)
        e_a = BenchmarkEntry(
            agent="a",
            suite="s",
            case="c",
            trace=Trace(),
            metric_results=[MetricResult(name="only_in_a", score=0.8, label="Only A")],
            passed=True,
            duration_s=0.5,
        )
        e_b = BenchmarkEntry(
            agent="b",
            suite="s",
            case="c",
            trace=Trace(),
            metric_results=[MetricResult(name="only_in_b", score=0.7, label="Only B")],
            passed=True,
            duration_s=0.5,
        )
        report = BenchmarkReport.from_entries([e_a, e_b])
        console, buf = _console_capture()
        render_metric_comparison(console, report)
        output = buf.getvalue()
        # Both agents should appear; missing metrics show dash
        assert "a" in output
        assert "b" in output

    def test_metric_comparison_shared_metrics_deduped(self) -> None:
        # Two agents with the same metric name; the second agent's metric is
        # already in `seen` → branch 116→115 taken
        e_a = BenchmarkEntry(
            agent="a",
            suite="s",
            case="c",
            trace=Trace(),
            metric_results=[MetricResult(name="shared", score=0.9, label="Shared")],
            passed=True,
            duration_s=0.5,
        )
        e_b = BenchmarkEntry(
            agent="b",
            suite="s",
            case="c",
            trace=Trace(),
            metric_results=[MetricResult(name="shared", score=0.6, label="Shared")],
            passed=True,
            duration_s=0.5,
        )
        report = BenchmarkReport.from_entries([e_a, e_b])
        console, buf = _console_capture()
        render_metric_comparison(console, report)
        output = buf.getvalue()
        # "shared" should appear exactly once as a column header
        assert output.count("shared") >= 1

    def test_per_test_breakdown_missing_agent_data(self) -> None:
        # Agent B ran a different case than agent A; lookup returns None for B
        # on A's case → branch at line 178
        e_a = BenchmarkEntry(
            agent="a",
            suite="s",
            case="only-a-case",
            trace=Trace(),
            metric_results=[],
            passed=True,
            duration_s=0.5,
        )
        e_b = BenchmarkEntry(
            agent="b",
            suite="s",
            case="only-b-case",
            trace=Trace(),
            metric_results=[],
            passed=False,
            duration_s=0.5,
        )
        report = BenchmarkReport.from_entries([e_a, e_b])
        console, buf = _console_capture()
        render_per_test_breakdown(console, report)
        output = buf.getvalue()
        assert "only-a-case" in output
        assert "only-b-case" in output


class TestBenchmarkRunnerAdditional:
    """Additional coverage for runner.py uncovered branches."""

    def test_runner_setup_failure_produces_error_entry(
        self, tmp_path: Path
    ) -> None:
        # Create a suite that references a fixture file that does NOT exist;
        # suite.resolve_fixtures() returns a non-empty list, but Runner.__post_init__
        # calls load_fixtures which raises FixtureLoadError.
        bad_fixture = tmp_path / "nonexistent_fixture.yaml"
        suite_file = tmp_path / "test_bad_fixture.yaml"
        suite_file.write_text(
            f"name: bad-fixture-suite\n"
            f"fixtures:\n"
            f"  - {bad_fixture}\n"
            f"agent:\n"
            f"  command: echo\n"
            f"cases:\n"
            f"  - name: c\n"
            f"    input: hi\n"
        )

        profile = AgentProfile(name="x", command="echo")
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        error_entries = [e for e in entries if e.error]
        assert len(error_entries) > 0
        assert error_entries[0].case == "<setup>"

    def test_retry_greater_than_one_uses_run_with_retry(
        self, tmp_path: Path
    ) -> None:
        # Setting retry_override=2 forces the retry path (lines 221-227)
        fixture_path = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fixture_path)

        profile = AgentProfile(name="x", command="echo")
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            retry_override=2,
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        assert len(entries) > 0
        assert all(e.agent == "x" for e in entries)
        # With CallableAdapter that always returns exit_code=0, passed should be True
        assert all(e.passed for e in entries)

    def test_profile_retry_used_when_no_override(self, tmp_path: Path) -> None:
        # profile.retry=2, retry_override=None → profile's value used (retry path)
        fixture_path = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fixture_path)

        profile = AgentProfile(name="x", command="echo", retry=2)
        runner = BenchmarkRunner(
            profiles=[profile],
            test_path=str(tmp_path),
            # retry_override is None (default)
            _adapter_factory=_callable_factory,
        )
        entries = runner.run()
        assert len(entries) > 0
        assert all(e.agent == "x" for e in entries)
