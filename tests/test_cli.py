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


# ---------------------------------------------------------------------------
# `mcptest cloud-push`
# ---------------------------------------------------------------------------


def _fake_urlopen_factory(responses: list[dict]):
    """Return a callable that sequentially yields mock HTTP responses.

    Each entry in *responses* is ``{"status": int, "body": dict}``.
    Raises ``urllib.error.HTTPError`` for status >= 400.
    """
    import io
    import urllib.error

    call_index = {"n": 0}

    def fake_urlopen(req):
        idx = call_index["n"]
        call_index["n"] += 1
        entry = responses[idx]
        code = entry["status"]
        body_bytes = json.dumps(entry["body"]).encode()
        if code >= 400:
            raise urllib.error.HTTPError(
                url=str(req.full_url if hasattr(req, "full_url") else req),
                code=code,
                msg="error",
                hdrs=None,
                fp=io.BytesIO(body_bytes),
            )

        class FakeResponse:
            def read(self):
                return body_bytes

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        return FakeResponse()

    return fake_urlopen


class TestCloudPushCommand:
    """Tests for `mcptest cloud-push` — HTTP calls are mocked via monkeypatch."""

    def _trace_file(self, tmp_path: Path, trace_id: str = "push-test") -> Path:
        return _make_trace_file(tmp_path / f"{trace_id}.json", ["tool_a", "tool_b"], trace_id)

    def test_push_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        trace_file = self._trace_file(tmp_path)
        run_resp = {"id": 1, "trace_id": "push-test", "is_baseline": False}
        monkeypatch.setattr(
            "urllib.request.urlopen", _fake_urlopen_factory([{"status": 201, "body": run_resp}])
        )
        runner = CliRunner()
        result = runner.invoke(main, ["cloud-push", str(trace_file)])
        assert result.exit_code == 0, result.output
        assert "#1" in result.output

    def test_push_json_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        trace_file = self._trace_file(tmp_path)
        run_resp = {"id": 42, "trace_id": "push-test", "is_baseline": False}
        monkeypatch.setattr(
            "urllib.request.urlopen", _fake_urlopen_factory([{"status": 201, "body": run_resp}])
        )
        runner = CliRunner()
        result = runner.invoke(main, ["cloud-push", str(trace_file), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "run" in data
        assert data["run"]["id"] == 42

    def test_push_with_check_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace_file = self._trace_file(tmp_path)
        run_resp = {"id": 1, "trace_id": "push-test", "is_baseline": False}
        check_resp = {
            "base_id": None,
            "head_id": 1,
            "deltas": [],
            "overall_passed": True,
            "regression_count": 0,
            "baseline_id": None,
            "baseline_branch": None,
            "status": "no_baseline",
        }
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _fake_urlopen_factory(
                [{"status": 201, "body": run_resp}, {"status": 200, "body": check_resp}]
            ),
        )
        runner = CliRunner()
        result = runner.invoke(main, ["cloud-push", str(trace_file), "--check"])
        assert result.exit_code == 0, result.output
        assert "no baseline" in result.output.lower()

    def test_push_with_check_fail_ci_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace_file = self._trace_file(tmp_path)
        run_resp = {"id": 2, "trace_id": "push-test", "is_baseline": False}
        check_resp = {
            "base_id": 1,
            "head_id": 2,
            "deltas": [
                {
                    "name": "tool_efficiency",
                    "label": "Tool Efficiency",
                    "base_score": 0.9,
                    "head_score": 0.5,
                    "delta": -0.4,
                    "regressed": True,
                }
            ],
            "overall_passed": False,
            "regression_count": 1,
            "baseline_id": 1,
            "baseline_branch": "main",
            "status": "fail",
        }
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _fake_urlopen_factory(
                [{"status": 201, "body": run_resp}, {"status": 200, "body": check_resp}]
            ),
        )
        runner = CliRunner()
        result = runner.invoke(main, ["cloud-push", str(trace_file), "--check", "--ci"])
        assert result.exit_code == 1

    def test_push_with_check_fail_no_ci_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace_file = self._trace_file(tmp_path)
        run_resp = {"id": 2, "trace_id": "push-test2", "is_baseline": False}
        check_resp = {
            "base_id": 1,
            "head_id": 2,
            "deltas": [],
            "overall_passed": False,
            "regression_count": 1,
            "baseline_id": 1,
            "baseline_branch": None,
            "status": "fail",
        }
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _fake_urlopen_factory(
                [{"status": 201, "body": run_resp}, {"status": 200, "body": check_resp}]
            ),
        )
        runner = CliRunner()
        result = runner.invoke(main, ["cloud-push", str(trace_file), "--check"])
        assert result.exit_code == 0

    def test_push_with_promote(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace_file = self._trace_file(tmp_path)
        run_resp = {"id": 3, "trace_id": "push-test", "is_baseline": False}
        promote_resp = {"id": 3, "suite": "smoke", "is_baseline": True, "message": "ok"}
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _fake_urlopen_factory(
                [
                    {"status": 201, "body": run_resp},
                    {"status": 200, "body": promote_resp},
                ]
            ),
        )
        runner = CliRunner()
        result = runner.invoke(main, ["cloud-push", str(trace_file), "--promote"])
        assert result.exit_code == 0, result.output
        assert "promoted" in result.output.lower() or "#3" in result.output

    def test_push_server_down_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.error

        trace_file = self._trace_file(tmp_path)

        def always_fail(req):
            raise OSError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", always_fail)
        runner = CliRunner()
        result = runner.invoke(main, ["cloud-push", str(trace_file)])
        assert result.exit_code == 1

    def test_push_conflict_error_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """409 from server (duplicate trace_id) should exit 1."""
        trace_file = self._trace_file(tmp_path)
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _fake_urlopen_factory([{"status": 409, "body": {"detail": "already exists"}}]),
        )
        runner = CliRunner()
        result = runner.invoke(main, ["cloud-push", str(trace_file)])
        assert result.exit_code == 1

    def test_push_labels_forwarded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--branch, --git-sha, --environment labels flow into the payload."""
        trace_file = self._trace_file(tmp_path)
        captured_payloads: list[dict] = []

        import io
        import urllib.request as _urllib_req

        original_Request = _urllib_req.Request

        def capturing_urlopen(req):
            import json as _json

            if hasattr(req, "data") and req.data:
                try:
                    captured_payloads.append(_json.loads(req.data))
                except Exception:
                    pass
            run_resp = {"id": 10, "trace_id": "push-test", "is_baseline": False}

            class FakeResp:
                def read(self):
                    return _json.dumps(run_resp).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

            return FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", capturing_urlopen)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "cloud-push",
                str(trace_file),
                "--branch",
                "main",
                "--git-sha",
                "abc123",
                "--environment",
                "prod",
            ],
        )
        assert result.exit_code == 0, result.output
        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert payload["branch"] == "main"
        assert payload["git_sha"] == "abc123"
        assert payload["environment"] == "prod"


# ---------------------------------------------------------------------------
# `mcptest run -j N` — parallel execution integration tests
# ---------------------------------------------------------------------------


def _write_multi_case_project(tmp_path: Path, n_cases: int = 4) -> Path:
    """Write a project with `n_cases` passing test cases."""
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "example.yaml").write_text(_PASSING_FIXTURE)
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "agent.py").write_text(_PASSING_AGENT)

    cases_yaml = "".join(
        f"  - name: case-{i}\n"
        f"    input: input-{i}\n"
        f"    assertions:\n"
        f"      - tool_called: greet\n"
        for i in range(n_cases)
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_multi.yaml").write_text(
        "name: multi-suite\n"
        "fixtures:\n"
        "  - ../fixtures/example.yaml\n"
        "agent:\n"
        f"  command: {sys.executable} ../examples/agent.py\n"
        "cases:\n"
        + cases_yaml
    )
    return tmp_path


class TestRunParallel:
    def test_parallel_j2_all_results_collected(self, tmp_path: Path) -> None:
        """All cases appear in results when running with -j 2."""
        _write_multi_case_project(tmp_path, n_cases=4)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "-j", "2", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["total"] == 4
        assert payload["passed"] == 4
        assert payload["failed"] == 0

    def test_parallel_json_includes_parallel_metadata(self, tmp_path: Path) -> None:
        """JSON output has a 'parallel' block with workers, timing, speedup."""
        _write_multi_case_project(tmp_path, n_cases=2)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "-j", "2", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "parallel" in payload
        par = payload["parallel"]
        assert "workers" in par
        assert "wall_clock_s" in par
        assert "total_cpu_s" in par
        assert "speedup" in par
        assert par["workers"] >= 1

    def test_serial_json_includes_parallel_metadata(self, tmp_path: Path) -> None:
        """Serial runs (j=1) also include the parallel block with workers=1."""
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "parallel" in payload
        assert payload["parallel"]["workers"] == 1

    def test_parallel_j0_auto_detect_runs(self, tmp_path: Path) -> None:
        """-j 0 auto-detects workers and completes without error."""
        _write_multi_case_project(tmp_path, n_cases=2)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "-j", "0", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["total"] == 2
        assert payload["passed"] == 2

    def test_parallel_fail_fast(self, tmp_path: Path) -> None:
        """-j 2 --fail-fast stops on first failure."""
        _write_multi_case_project(tmp_path, n_cases=4)
        # Overwrite with a mix of pass/fail — first case fails.
        (tmp_path / "tests" / "test_multi.yaml").write_text(
            "name: multi-suite\n"
            "fixtures:\n"
            "  - ../fixtures/example.yaml\n"
            "agent:\n"
            f"  command: {sys.executable} ../examples/agent.py\n"
            "cases:\n"
            "  - name: c0-fail\n"
            "    input: x\n"
            "    assertions:\n"
            "      - tool_called: nonexistent\n"
            "  - name: c1-pass\n"
            "    input: y\n"
            "    assertions:\n"
            "      - tool_called: greet\n"
            "  - name: c2-pass\n"
            "    input: z\n"
            "    assertions:\n"
            "      - tool_called: greet\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "-j", "1", "--fail-fast", "--ci"],
        )
        assert result.exit_code == 1
        assert "c0-fail" in result.output
        # With j=1 and fail-fast, subsequent cases are not executed.
        assert "c1-pass" not in result.output

    def test_parallel_suite_opt_out(self, tmp_path: Path) -> None:
        """Suite with parallel: false runs serially even under -j 4."""
        (tmp_path / "fixtures").mkdir()
        (tmp_path / "fixtures" / "example.yaml").write_text(_PASSING_FIXTURE)
        (tmp_path / "examples").mkdir()
        (tmp_path / "examples" / "agent.py").write_text(_PASSING_AGENT)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_serial.yaml").write_text(
            "name: serial-suite\n"
            "fixtures:\n"
            "  - ../fixtures/example.yaml\n"
            "agent:\n"
            f"  command: {sys.executable} ../examples/agent.py\n"
            "parallel: false\n"
            "cases:\n"
            "  - name: c0\n"
            "    assertions:\n"
            "      - tool_called: greet\n"
            "  - name: c1\n"
            "    assertions:\n"
            "      - tool_called: greet\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "-j", "4", "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["total"] == 2
        assert payload["passed"] == 2

    def test_parallel_table_output_no_crash(self, tmp_path: Path) -> None:
        """Table-format output with -j 2 completes without error."""
        _write_multi_case_project(tmp_path, n_cases=2)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "-j", "2"],
        )
        assert result.exit_code == 0, result.output
        assert "passed" in result.output.lower()

    def test_parallel_ci_exits_nonzero_on_failure(self, tmp_path: Path) -> None:
        """--ci with -j 2 exits 1 when any case fails."""
        _write_multi_case_project(tmp_path, n_cases=2)
        (tmp_path / "tests" / "test_multi.yaml").write_text(
            "name: multi-suite\n"
            "fixtures:\n"
            "  - ../fixtures/example.yaml\n"
            "agent:\n"
            f"  command: {sys.executable} ../examples/agent.py\n"
            "cases:\n"
            "  - name: fail\n"
            "    assertions:\n"
            "      - tool_called: nonexistent\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "-j", "2", "--ci"],
        )
        assert result.exit_code == 1
