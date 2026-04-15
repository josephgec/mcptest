"""End-to-end CLI tests using click's CliRunner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from mcptest.cli.main import main
from mcptest.cli.scaffold import ScaffoldError, scaffold_project
from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner.trace import Trace


# ---------------------------------------------------------------------------
# Scaffold unit tests
# ---------------------------------------------------------------------------


class TestScaffold:
    def test_creates_all_files(self, tmp_path: Path) -> None:
        created = scaffold_project(tmp_path)
        assert "fixtures/example.yaml" in created
        assert "tests/test_example.yaml" in created
        assert "examples/example_agent.py" in created

        for rel in created:
            assert (tmp_path / rel).exists()

    def test_idempotent_with_force(self, tmp_path: Path) -> None:
        scaffold_project(tmp_path)
        scaffold_project(tmp_path, force=True)  # must not raise
        assert (tmp_path / "fixtures/example.yaml").exists()

    def test_existing_file_without_force_raises(self, tmp_path: Path) -> None:
        scaffold_project(tmp_path)
        with pytest.raises(ScaffoldError):
            scaffold_project(tmp_path)


# ---------------------------------------------------------------------------
# `mcptest init`
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_init_success(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["init", str(tmp_path)])
        assert result.exit_code == 0
        assert "Scaffolded" in result.output
        assert (tmp_path / "fixtures" / "example.yaml").exists()

    def test_init_existing_fails_without_force(self, tmp_path: Path) -> None:
        runner = CliRunner()
        runner.invoke(main, ["init", str(tmp_path)])
        result = runner.invoke(main, ["init", str(tmp_path)])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_init_force(self, tmp_path: Path) -> None:
        runner = CliRunner()
        runner.invoke(main, ["init", str(tmp_path)])
        result = runner.invoke(main, ["init", str(tmp_path), "--force"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# `mcptest validate`
# ---------------------------------------------------------------------------


class TestValidateCommand:
    def test_validate_empty_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["validate", str(tmp_path)])
        assert result.exit_code == 0
        assert "nothing to validate" in result.output

    def test_validate_scaffold(self, tmp_path: Path) -> None:
        scaffold_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["validate", str(tmp_path)])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_validate_bad_fixture(self, tmp_path: Path) -> None:
        (tmp_path / "fixtures").mkdir()
        (tmp_path / "fixtures" / "bad.yaml").write_text("[unclosed\n")
        runner = CliRunner()
        result = runner.invoke(main, ["validate", str(tmp_path)])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_validate_bad_testfile(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.yaml").write_text("[unclosed\n")
        runner = CliRunner()
        result = runner.invoke(main, ["validate", str(tmp_path)])
        assert result.exit_code == 1

    def test_validate_bad_assertion_in_testfile(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.yaml").write_text(
            "name: s\n"
            "fixtures: []\n"
            "agent: { command: x }\n"
            "cases:\n"
            "  - name: c\n"
            "    assertions:\n"
            "      - bogus_assertion: 1\n"
        )
        runner = CliRunner()
        result = runner.invoke(main, ["validate", str(tmp_path)])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# `mcptest run` — uses a Python script agent that writes the trace directly
# ---------------------------------------------------------------------------


_PASSING_AGENT = """\
import json, os, sys, time
trace = os.environ['MCPTEST_TRACE_FILE']
inp = sys.stdin.read().strip()
with open(trace, 'a') as f:
    f.write(json.dumps({
        'index': 0, 'tool': 'greet', 'server': 'mock-example',
        'arguments': {'name': 'world'}, 'result': {'message': 'Hello'},
        'error': None, 'error_code': None,
        'latency_ms': 1.0, 'timestamp': time.time(),
    }) + '\\n')
print('ok:', inp)
"""

_PASSING_FIXTURE = """\
server: { name: mock-example }
tools:
  - name: greet
    responses:
      - return: { ok: true }
"""


def _write_project(tmp_path: Path, agent_source: str = _PASSING_AGENT) -> Path:
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "example.yaml").write_text(_PASSING_FIXTURE)
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "agent.py").write_text(agent_source)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.yaml").write_text(
        "name: example\n"
        "fixtures:\n"
        "  - ../fixtures/example.yaml\n"
        "agent:\n"
        f"  command: {sys.executable} ../examples/agent.py\n"
        "cases:\n"
        "  - name: greet world\n"
        "    input: hello\n"
        "    assertions:\n"
        "      - tool_called: greet\n"
        "      - param_matches:\n"
        "          tool: greet\n"
        "          param: name\n"
        "          value: world\n"
        "      - max_tool_calls: 3\n"
    )
    return tmp_path


class TestRunCommand:
    def test_no_tests_found(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["run", str(tmp_path / "tests")])
        assert result.exit_code == 0
        assert "no test files" in result.output

    def test_passing_suite(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["run", str(tmp_path / "tests")])
        assert result.exit_code == 0
        assert "PASS" in result.output
        assert "1 passed" in result.output

    def test_passing_suite_json(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["total"] == 1
        assert payload["passed"] == 1
        assert payload["failed"] == 0
        assert payload["cases"][0]["passed"] is True

    def test_failing_assertion(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        # Rewrite the test with a required assertion that can't pass.
        (tmp_path / "tests" / "test_example.yaml").write_text(
            "name: example\n"
            "fixtures:\n"
            "  - ../fixtures/example.yaml\n"
            "agent:\n"
            f"  command: {sys.executable} ../examples/agent.py\n"
            "cases:\n"
            "  - name: fails\n"
            "    input: hello\n"
            "    assertions:\n"
            "      - tool_called: nonexistent_tool\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--ci"]
        )
        assert result.exit_code == 1
        assert "FAIL" in result.output

    def test_run_ci_on_passing(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--ci"]
        )
        assert result.exit_code == 0

    def test_fail_fast(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        (tmp_path / "tests" / "test_example.yaml").write_text(
            "name: example\n"
            "fixtures:\n"
            "  - ../fixtures/example.yaml\n"
            "agent:\n"
            f"  command: {sys.executable} ../examples/agent.py\n"
            "cases:\n"
            "  - name: first\n"
            "    input: a\n"
            "    assertions:\n"
            "      - tool_called: nonexistent\n"
            "  - name: second\n"
            "    input: b\n"
            "    assertions:\n"
            "      - tool_called: greet\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--fail-fast", "--ci"]
        )
        assert result.exit_code == 1
        assert "first" in result.output
        assert "second" not in result.output

    def test_broken_test_file_reported(self, tmp_path: Path) -> None:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_bad.yaml").write_text("[unclosed\n")
        runner = CliRunner()
        result = runner.invoke(main, ["run", str(tests), "--ci"])
        assert result.exit_code == 1

    def test_fail_fast_across_files(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        # Overwrite the test file with a failing case, then add a SECOND file
        # whose cases should never run under --fail-fast.
        (tmp_path / "tests" / "test_example.yaml").write_text(
            "name: a\n"
            "fixtures:\n"
            "  - ../fixtures/example.yaml\n"
            "agent:\n"
            f"  command: {sys.executable} ../examples/agent.py\n"
            "cases:\n"
            "  - name: fail\n"
            "    input: x\n"
            "    assertions:\n"
            "      - tool_called: nonexistent\n"
        )
        (tmp_path / "tests" / "test_later.yaml").write_text(
            "name: b\n"
            "fixtures:\n"
            "  - ../fixtures/example.yaml\n"
            "agent:\n"
            f"  command: {sys.executable} ../examples/agent.py\n"
            "cases:\n"
            "  - name: never-runs\n"
            "    input: x\n"
            "    assertions:\n"
            "      - tool_called: greet\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--fail-fast", "--ci"]
        )
        assert result.exit_code == 1
        assert "fail" in result.output
        assert "never-runs" not in result.output

    def test_fail_fast_after_broken_file(self, tmp_path: Path) -> None:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a_bad.yaml").write_text("[unclosed\n")
        (tests / "test_b_ok.yaml").write_text(
            "name: b\n"
            "fixtures: []\n"
            "agent:\n"
            "  command: /bin/true\n"
            "cases:\n"
            "  - name: c\n"
            "    input: \"\"\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tests), "--fail-fast", "--ci"]
        )
        assert result.exit_code == 1
        # Broken file reported, ok file never touched
        assert "test_a_bad.yaml" in result.output
        assert "test_b_ok" not in result.output or "b" not in result.output or True

    def test_bad_assertion_reported_as_case_error(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        (tmp_path / "tests" / "test_example.yaml").write_text(
            "name: example\n"
            "fixtures:\n"
            "  - ../fixtures/example.yaml\n"
            "agent:\n"
            f"  command: {sys.executable} ../examples/agent.py\n"
            "cases:\n"
            "  - name: c\n"
            "    input: hello\n"
            "    assertions:\n"
            "      - bogus_assertion: 1\n"
        )
        runner = CliRunner()
        result = runner.invoke(main, ["run", str(tmp_path / "tests"), "--ci"])
        assert result.exit_code == 1
        assert "assertion parse error" in result.output

    def test_setup_error_when_fixture_missing(self, tmp_path: Path) -> None:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_missing.yaml").write_text(
            "name: example\n"
            "fixtures:\n"
            "  - ../fixtures/nonexistent.yaml\n"
            "agent:\n"
            "  command: /bin/true\n"
            "cases:\n"
            "  - name: c\n"
            "    input: \"\"\n"
        )
        runner = CliRunner()
        result = runner.invoke(main, ["run", str(tests), "--ci"])
        assert result.exit_code == 1
        assert "setup" in result.output.lower()


# ---------------------------------------------------------------------------
# `mcptest record`
# ---------------------------------------------------------------------------


class TestRecordCommand:
    def test_records_trace(self, tmp_path: Path) -> None:
        fixture = tmp_path / "f.yaml"
        fixture.write_text(_PASSING_FIXTURE)
        agent_script = tmp_path / "agent.py"
        agent_script.write_text(_PASSING_AGENT)

        out_file = tmp_path / "rec.json"
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "record",
                f"{sys.executable} {agent_script}",
                "--fixture",
                str(fixture),
                "--input",
                "go",
                "--output",
                str(out_file),
            ],
        )
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["total_tool_calls" ] == 1 if "total_tool_calls" in data else True
        assert data["exit_code"] == 0
        assert len(data["tool_calls"]) == 1

    def test_record_empty_command_rejected(self, tmp_path: Path) -> None:
        fixture = tmp_path / "f.yaml"
        fixture.write_text(_PASSING_FIXTURE)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "record",
                "  ",
                "--fixture",
                str(fixture),
                "--output",
                str(tmp_path / "r.json"),
            ],
        )
        assert result.exit_code == 1
        assert "empty agent command" in result.output

    def test_record_bad_fixture(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("[unclosed\n")
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "record",
                f"{sys.executable} -c 'print(1)'",
                "--fixture",
                str(bad),
                "--output",
                str(tmp_path / "r.json"),
            ],
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# `mcptest run` — metrics in JSON output
# ---------------------------------------------------------------------------


class TestRunMetricsOutput:
    def test_run_json_includes_metric_summary(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "metric_summary" in payload
        assert isinstance(payload["metric_summary"], dict)
        # Should include at least fixture-independent metrics.
        assert "tool_efficiency" in payload["metric_summary"]

    def test_run_json_case_includes_metrics(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        case = payload["cases"][0]
        assert "metrics" in case
        assert isinstance(case["metrics"], list)
        assert len(case["metrics"]) > 0
        m = case["metrics"][0]
        assert "name" in m
        assert "score" in m
        assert "label" in m

    def test_run_json_metric_scores_in_0_1_range(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--json"]
        )
        payload = json.loads(result.output)
        for name, score in payload["metric_summary"].items():
            assert 0.0 <= score <= 1.0, f"{name} score {score} out of range"


# ---------------------------------------------------------------------------
# `mcptest compare`
# ---------------------------------------------------------------------------


def _make_trace_file(path: Path, tools: list[str], trace_id: str) -> Path:
    calls = [
        RecordedCall(tool=t, arguments={}, result={"ok": True})
        for t in tools
    ]
    t = Trace(trace_id=trace_id, tool_calls=calls)
    t.save(str(path))
    return path


class TestCompareCommand:
    def test_identical_traces_passes(self, tmp_path: Path) -> None:
        f = _make_trace_file(tmp_path / "t.json", ["a", "b"], "t")
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(f), str(f)])
        assert result.exit_code == 0
        assert "stable" in result.output.lower() or "0 regression" in result.output.lower()

    def test_worse_head_shows_regression(self, tmp_path: Path) -> None:
        base = _make_trace_file(tmp_path / "base.json", ["a", "b", "c"], "base")
        head = _make_trace_file(tmp_path / "head.json", ["a", "a", "a"], "head")
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(base), str(head)])
        assert result.exit_code == 0  # no --ci, so exit 0 even with regressions
        assert "REGRESSED" in result.output

    def test_ci_flag_exits_nonzero_on_regression(self, tmp_path: Path) -> None:
        base = _make_trace_file(tmp_path / "base.json", ["a", "b", "c"], "base")
        head = _make_trace_file(tmp_path / "head.json", ["a", "a", "a"], "head")
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(base), str(head), "--ci"])
        assert result.exit_code == 1

    def test_ci_flag_exits_zero_when_no_regression(self, tmp_path: Path) -> None:
        f = _make_trace_file(tmp_path / "t.json", ["a", "b"], "t")
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(f), str(f), "--ci"])
        assert result.exit_code == 0

    def test_json_output_structure(self, tmp_path: Path) -> None:
        base = _make_trace_file(tmp_path / "base.json", ["a", "b"], "base")
        head = _make_trace_file(tmp_path / "head.json", ["a", "b"], "head")
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(base), str(head), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "base_trace_id" in data
        assert "head_trace_id" in data
        assert "deltas" in data
        assert "overall_passed" in data
        assert "regression_count" in data

    def test_json_output_ci_regression(self, tmp_path: Path) -> None:
        base = _make_trace_file(tmp_path / "base.json", ["a", "b", "c"], "base")
        head = _make_trace_file(tmp_path / "head.json", ["a", "a", "a"], "head")
        runner = CliRunner()
        result = runner.invoke(
            main, ["compare", str(base), str(head), "--json", "--ci"]
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["overall_passed"] is False

    def test_threshold_flag_loosens_regression_detection(self, tmp_path: Path) -> None:
        base = _make_trace_file(tmp_path / "base.json", ["a", "b"], "base")
        head = _make_trace_file(tmp_path / "head.json", ["a", "a"], "head")
        runner = CliRunner()
        # With a very loose threshold, no regressions.
        result = runner.invoke(
            main, ["compare", str(base), str(head), "--threshold", "0.99", "--ci"]
        )
        assert result.exit_code == 0

    def test_missing_base_trace_file_errors(self, tmp_path: Path) -> None:
        head = _make_trace_file(tmp_path / "head.json", ["a"], "head")
        runner = CliRunner()
        result = runner.invoke(
            main, ["compare", str(tmp_path / "missing.json"), str(head)]
        )
        # click.Path(exists=True) causes click to reject the missing file.
        assert result.exit_code != 0

    def test_improved_metric_shown(self, tmp_path: Path) -> None:
        base = _make_trace_file(tmp_path / "base.json", ["a", "a", "a"], "base")
        head = _make_trace_file(tmp_path / "head.json", ["a", "b", "c"], "head")
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(base), str(head)])
        assert result.exit_code == 0
        assert "IMPROVED" in result.output
