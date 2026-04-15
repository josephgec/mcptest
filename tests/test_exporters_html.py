"""Tests for the HTML self-contained report exporter.

Session 18: Self-Contained HTML Test Reports
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from mcptest.assertions.base import AssertionResult
from mcptest.cli.commands import CaseResult
from mcptest.cli.main import main
from mcptest.exporters import HtmlExporter, get_exporter
from mcptest.exporters.base import EXPORTERS
from mcptest.metrics.base import MetricResult
from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner.trace import Trace


# ---------------------------------------------------------------------------
# Shared helpers (mirror test_exporters.py pattern)
# ---------------------------------------------------------------------------


def _trace(
    *,
    trace_id: str = "abc123",
    duration_s: float = 0.5,
    exit_code: int = 0,
    agent_error: str | None = None,
    tool_calls: list[RecordedCall] | None = None,
) -> Trace:
    return Trace(
        trace_id=trace_id,
        input="hello",
        output="world",
        tool_calls=tool_calls or [],
        duration_s=duration_s,
        exit_code=exit_code,
        agent_error=agent_error,
    )


def _assertion(
    passed: bool,
    name: str = "tool_called",
    message: str = "",
    details: dict[str, Any] | None = None,
) -> AssertionResult:
    if not message:
        message = "ok" if passed else "assertion failed"
    return AssertionResult(
        passed=passed,
        name=name,
        message=message,
        details=details or ({} if passed else {"expected": "greet", "got": "none"}),
    )


def _metric(name: str = "tool_efficiency", score: float = 0.9) -> MetricResult:
    return MetricResult(
        name=name,
        score=score,
        label="excellent" if score >= 0.8 else "poor",
        details={},
    )


def _recorded_call(
    tool: str = "greet",
    arguments: dict[str, Any] | None = None,
    result: Any | None = None,
    error: str | None = None,
    server_name: str = "mock",
    latency_ms: float = 5.0,
) -> RecordedCall:
    return RecordedCall(
        tool=tool,
        arguments=arguments or {"name": "world"},
        result=result,
        error=error,
        server_name=server_name,
        latency_ms=latency_ms,
    )


def _passing(suite: str = "my_suite", case: str = "my_case") -> CaseResult:
    return CaseResult(
        suite_name=suite,
        case_name=case,
        trace=_trace(),
        assertion_results=[_assertion(True)],
        metrics=[_metric()],
    )


def _failing_assert(suite: str = "my_suite", case: str = "my_case") -> CaseResult:
    return CaseResult(
        suite_name=suite,
        case_name=case,
        trace=_trace(),
        assertion_results=[_assertion(False, name="tool_called", message="greet not called")],
    )


def _runner_error(suite: str = "my_suite", case: str = "error_case") -> CaseResult:
    return CaseResult(
        suite_name=suite,
        case_name=case,
        trace=_trace(),
        assertion_results=[],
        error="fixture not found: example.yaml",
    )


def _agent_error(suite: str = "my_suite", case: str = "agent_error_case") -> CaseResult:
    return CaseResult(
        suite_name=suite,
        case_name=case,
        trace=_trace(exit_code=1, agent_error="agent crashed"),
        assertion_results=[],
    )


# ---------------------------------------------------------------------------
# HtmlExporter unit tests
# ---------------------------------------------------------------------------


class TestHtmlExporter:
    exporter = HtmlExporter()

    # -- Basic structure --

    def test_returns_string(self) -> None:
        result = self.exporter.export([])
        assert isinstance(result, str)

    def test_html_doctype_present(self) -> None:
        result = self.exporter.export([])
        assert result.startswith("<!DOCTYPE html>")

    def test_html_root_element(self) -> None:
        result = self.exporter.export([])
        assert "<html" in result
        assert "</html>" in result

    def test_head_and_body_present(self) -> None:
        result = self.exporter.export([])
        assert "<head>" in result
        assert "<body>" in result

    def test_inline_css_present(self) -> None:
        result = self.exporter.export([])
        assert "<style>" in result

    def test_inline_js_present(self) -> None:
        result = self.exporter.export([])
        assert "<script>" in result

    def test_charset_meta(self) -> None:
        result = self.exporter.export([])
        assert 'charset="UTF-8"' in result

    # -- Empty results --

    def test_empty_results_valid_html(self) -> None:
        result = self.exporter.export([])
        assert "0" in result  # 0 tests
        assert "<!DOCTYPE html>" in result

    def test_empty_results_shows_zero_total(self) -> None:
        result = self.exporter.export([])
        # Summary bar has total count
        assert ">0<" in result

    def test_empty_results_no_metric_section(self) -> None:
        result = self.exporter.export([])
        # No metric overview section when there are no metrics
        assert "Metric Overview" not in result

    # -- Passing case --

    def test_single_passing_case_pass_badge(self) -> None:
        result = self.exporter.export([_passing()])
        assert "PASS" in result

    def test_single_passing_case_pass_count(self) -> None:
        result = self.exporter.export([_passing()])
        assert "pill-pass" in result

    def test_single_passing_case_suite_name(self) -> None:
        result = self.exporter.export([_passing(suite="cool_suite")])
        assert "cool_suite" in result

    def test_single_passing_case_case_name(self) -> None:
        result = self.exporter.export([_passing(case="say_hello")])
        assert "say_hello" in result

    def test_passing_total_and_passed_counts(self) -> None:
        results = [_passing(), _passing(case="c2")]
        result = self.exporter.export(results)
        assert "pill-pass" in result

    # -- Failing assertion --

    def test_failing_assertion_fail_badge(self) -> None:
        result = self.exporter.export([_failing_assert()])
        assert "FAIL" in result

    def test_failing_assertion_pill_class(self) -> None:
        result = self.exporter.export([_failing_assert()])
        assert "pill-fail" in result

    def test_failing_assertion_message_rendered(self) -> None:
        result = self.exporter.export([_failing_assert()])
        assert "greet not called" in result

    def test_failing_assertion_details_rendered(self) -> None:
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[
                _assertion(False, details={"expected": "greet", "got": "none"})
            ],
        )
        result = self.exporter.export([case])
        assert "expected" in result
        assert "greet" in result

    # -- Agent error --

    def test_agent_error_error_badge(self) -> None:
        result = self.exporter.export([_agent_error()])
        assert "ERROR" in result

    def test_agent_error_pill_class(self) -> None:
        result = self.exporter.export([_agent_error()])
        assert "pill-error" in result

    def test_agent_error_message_in_detail(self) -> None:
        result = self.exporter.export([_agent_error()])
        assert "agent crashed" in result

    # -- Runner error --

    def test_runner_error_badge(self) -> None:
        result = self.exporter.export([_runner_error()])
        assert "ERROR" in result

    def test_runner_error_message_in_detail(self) -> None:
        result = self.exporter.export([_runner_error()])
        assert "fixture not found" in result

    # -- Multiple suites --

    def test_multiple_suites_both_names_present(self) -> None:
        results = [
            _passing(suite="suite_alpha", case="c1"),
            _failing_assert(suite="suite_beta", case="c2"),
        ]
        result = self.exporter.export(results)
        assert "suite_alpha" in result
        assert "suite_beta" in result

    def test_multiple_suites_correct_counts(self) -> None:
        results = [_passing(), _passing(case="c2"), _failing_assert(case="c3")]
        result = self.exporter.export(results)
        # 2 pass, 1 fail shown in filter buttons
        assert "Passing (2)" in result
        assert "Failing (1)" in result

    # -- Metrics --

    def test_metric_overview_section_present_when_metrics_exist(self) -> None:
        result = self.exporter.export([_passing()])  # _passing includes a metric
        assert "Metric Overview" in result

    def test_metric_name_in_overview(self) -> None:
        result = self.exporter.export([_passing()])
        assert "tool_efficiency" in result

    def test_metric_good_color_class(self) -> None:
        # score=0.9 → metric-good
        result = self.exporter.export([_passing()])
        assert "metric-good" in result

    def test_metric_warn_color_class(self) -> None:
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[_assertion(True)],
            metrics=[_metric(score=0.6)],
        )
        result = self.exporter.export([case])
        assert "metric-warn" in result

    def test_metric_bad_color_class(self) -> None:
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[_assertion(True)],
            metrics=[_metric(score=0.3)],
        )
        result = self.exporter.export([case])
        assert "metric-bad" in result

    def test_multiple_metrics_all_shown(self) -> None:
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[_assertion(True)],
            metrics=[
                _metric("tool_efficiency", 0.9),
                _metric("output_quality", 0.7),
                _metric("step_count", 0.4),
            ],
        )
        result = self.exporter.export([case])
        assert "tool_efficiency" in result
        assert "output_quality" in result
        assert "step_count" in result

    def test_no_metrics_no_metric_section(self) -> None:
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[_assertion(True)],
            metrics=[],
        )
        result = self.exporter.export([case])
        assert "Metric Overview" not in result

    # -- Tool calls --

    def test_tool_calls_rendered_in_detail(self) -> None:
        call = _recorded_call(tool="greet", arguments={"name": "Alice"})
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(tool_calls=[call]),
            assertion_results=[_assertion(True)],
        )
        result = self.exporter.export([case])
        assert "greet" in result
        assert "Tool Calls" in result

    def test_tool_call_arguments_rendered(self) -> None:
        call = _recorded_call(arguments={"name": "Alice"})
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(tool_calls=[call]),
            assertion_results=[_assertion(True)],
        )
        result = self.exporter.export([case])
        assert "Alice" in result

    def test_tool_call_error_highlighted(self) -> None:
        call = _recorded_call(tool="broken", error="tool timed out")
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(tool_calls=[call]),
            assertion_results=[_assertion(True)],
        )
        result = self.exporter.export([case])
        assert "tool-error" in result
        assert "tool timed out" in result

    def test_tool_call_count_in_table_row(self) -> None:
        calls = [_recorded_call(), _recorded_call(tool="farewell")]
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(tool_calls=calls),
            assertion_results=[_assertion(True)],
        )
        result = self.exporter.export([case])
        # data-tools="2" appears in the case row
        assert 'data-tools="2"' in result

    # -- XSS safety --

    def test_escapes_case_name_with_script_tag(self) -> None:
        case = CaseResult(
            suite_name="safe_suite",
            case_name='<script>alert("xss")</script>',
            trace=_trace(),
            assertion_results=[_assertion(True)],
        )
        result = self.exporter.export([case])
        assert "<script>alert" not in result
        assert "&lt;script&gt;" in result

    def test_escapes_suite_name_with_html(self) -> None:
        case = CaseResult(
            suite_name='<b>bold</b>',
            case_name="c",
            trace=_trace(),
            assertion_results=[_assertion(True)],
        )
        result = self.exporter.export([case])
        assert "<b>bold</b>" not in result
        assert "&lt;b&gt;" in result

    def test_escapes_assertion_message(self) -> None:
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[
                _assertion(False, message='<img src=x onerror=alert(1)>')
            ],
        )
        result = self.exporter.export([case])
        assert "<img src=x" not in result
        assert "&lt;img" in result

    def test_escapes_runner_error_message(self) -> None:
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[],
            error='<script>evil()</script>',
        )
        result = self.exporter.export([case])
        assert "<script>evil" not in result

    def test_escapes_tool_call_arguments(self) -> None:
        call = _recorded_call(arguments={"payload": '<script>hack()</script>'})
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(tool_calls=[call]),
            assertion_results=[_assertion(True)],
        )
        result = self.exporter.export([case])
        assert "<script>hack" not in result

    # -- Suite name parameter --

    def test_suite_name_appears_in_title(self) -> None:
        result = self.exporter.export([], suite_name="my-project")
        assert "my-project" in result

    def test_default_suite_name_mcptest(self) -> None:
        result = self.exporter.export([])
        assert "mcptest" in result

    # -- Summary counts --

    def test_summary_error_count(self) -> None:
        results = [_passing(), _runner_error(), _agent_error()]
        result = self.exporter.export(results)
        # 2 errors shown in filter button
        assert "Errors (2)" in result

    def test_duration_in_summary(self) -> None:
        results = [
            CaseResult(
                suite_name="s", case_name="c",
                trace=_trace(duration_s=1.5),
                assertion_results=[_assertion(True)],
            )
        ]
        result = self.exporter.export(results)
        assert "1.5s" in result

    # -- Data attributes for JS --

    def test_case_row_has_data_status_pass(self) -> None:
        result = self.exporter.export([_passing()])
        assert 'data-status="pass"' in result

    def test_case_row_has_data_status_fail(self) -> None:
        result = self.exporter.export([_failing_assert()])
        assert 'data-status="fail"' in result

    def test_case_row_has_data_status_error(self) -> None:
        result = self.exporter.export([_runner_error()])
        assert 'data-status="error"' in result

    def test_detail_row_id_matches_case_row(self) -> None:
        result = self.exporter.export([_passing()])
        assert 'data-case="0"' in result
        assert 'id="detail-0"' in result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestHtmlRegistry:
    def test_html_registered(self) -> None:
        assert "html" in EXPORTERS

    def test_get_exporter_returns_html_instance(self) -> None:
        assert isinstance(get_exporter("html"), HtmlExporter)

    def test_each_call_returns_fresh_instance(self) -> None:
        assert get_exporter("html") is not get_exporter("html")


# ---------------------------------------------------------------------------
# CLI integration — `mcptest run --format html`
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


def _write_project(tmp_path: Path) -> Path:
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "example.yaml").write_text(_PASSING_FIXTURE)
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "agent.py").write_text(_PASSING_AGENT)
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
    )
    return tmp_path


class TestCLIHtmlFormat:
    def test_run_format_html_creates_file(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        report = tmp_path / "report.html"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "--format", "html", "--output", str(report)],
        )
        assert result.exit_code == 0, result.output
        assert report.exists()

    def test_run_format_html_file_is_valid_html(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        report = tmp_path / "report.html"
        runner = CliRunner()
        runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "--format", "html", "--output", str(report)],
        )
        content = report.read_text(encoding="utf-8")
        assert content.startswith("<!DOCTYPE html>")
        assert "<html" in content

    def test_run_format_html_default_filename(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        import os
        orig = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(
                main,
                ["run", str(tmp_path / "tests"), "--format", "html"],
            )
        finally:
            os.chdir(orig)
        assert result.exit_code == 0, result.output
        assert (tmp_path / "mcptest-report.html").exists()

    def test_run_format_html_contains_pass(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        report = tmp_path / "report.html"
        runner = CliRunner()
        runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "--format", "html", "--output", str(report)],
        )
        content = report.read_text(encoding="utf-8")
        assert "PASS" in content

    def test_run_format_html_stderr_message(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        report = tmp_path / "report.html"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["run", str(tmp_path / "tests"), "--format", "html", "--output", str(report)],
        )
        # CliRunner captures stderr in output when mix_stderr is the default (True).
        assert "HTML report written to" in result.output


class TestExportCommandHtml:
    def _write_results_json(self, tmp_path: Path, results: list[CaseResult]) -> Path:
        payload = {
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "total": len(results),
            "cases": [r.to_dict() for r in results],
            "metric_summary": {},
        }
        p = tmp_path / "results.json"
        p.write_text(json.dumps(payload))
        return p

    def test_export_to_html_creates_file(self, tmp_path: Path) -> None:
        results = [_passing(), _failing_assert()]
        json_file = self._write_results_json(tmp_path, results)
        report = tmp_path / "out.html"
        runner = CliRunner()
        result = runner.invoke(
            main, ["export", str(json_file), "--format", "html", "--output", str(report)]
        )
        assert result.exit_code == 0, result.output
        assert report.exists()

    def test_export_to_html_valid_structure(self, tmp_path: Path) -> None:
        results = [_passing(), _failing_assert()]
        json_file = self._write_results_json(tmp_path, results)
        report = tmp_path / "out.html"
        runner = CliRunner()
        runner.invoke(
            main, ["export", str(json_file), "--format", "html", "--output", str(report)]
        )
        content = report.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "PASS" in content
        assert "FAIL" in content

    def test_export_to_html_default_filename(self, tmp_path: Path) -> None:
        results = [_passing()]
        json_file = self._write_results_json(tmp_path, results)
        runner = CliRunner()
        import os
        orig = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = runner.invoke(
                main, ["export", str(json_file), "--format", "html"]
            )
        finally:
            os.chdir(orig)
        assert result.exit_code == 0, result.output
        assert (tmp_path / "mcptest-report.html").exists()

    def test_export_to_html_with_metrics(self, tmp_path: Path) -> None:
        case = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[_assertion(True)],
            metrics=[_metric("tool_efficiency", 0.85)],
        )
        json_file = self._write_results_json(tmp_path, [case])
        report = tmp_path / "out.html"
        runner = CliRunner()
        runner.invoke(
            main, ["export", str(json_file), "--format", "html", "--output", str(report)]
        )
        content = report.read_text(encoding="utf-8")
        assert "tool_efficiency" in content
        assert "Metric Overview" in content

    def test_export_html_roundtrip_all_statuses(self, tmp_path: Path) -> None:
        results = [_passing(), _failing_assert(), _runner_error()]
        json_file = self._write_results_json(tmp_path, results)
        report = tmp_path / "out.html"
        runner = CliRunner()
        result = runner.invoke(
            main, ["export", str(json_file), "--format", "html", "--output", str(report)]
        )
        assert result.exit_code == 0
        content = report.read_text(encoding="utf-8")
        assert "PASS" in content
        assert "FAIL" in content
        assert "ERROR" in content
