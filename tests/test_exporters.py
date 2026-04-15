"""Tests for the CI output format exporters (JUnit XML and TAP v14).

Session 16: Standard CI Export Formats
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from mcptest.assertions.base import AssertionResult
from mcptest.cli.commands import CaseResult
from mcptest.cli.main import main
from mcptest.exporters import JUnitExporter, TAPExporter, get_exporter
from mcptest.exporters.base import EXPORTERS
from mcptest.metrics.base import MetricResult
from mcptest.runner.trace import Trace


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _trace(
    *,
    trace_id: str = "abc123",
    duration_s: float = 0.5,
    exit_code: int = 0,
    agent_error: str | None = None,
) -> Trace:
    return Trace(
        trace_id=trace_id,
        input="hello",
        output="world",
        tool_calls=[],
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


def _passing(
    suite: str = "my_suite",
    case: str = "my_case",
    duration: float = 0.5,
) -> CaseResult:
    return CaseResult(
        suite_name=suite,
        case_name=case,
        trace=_trace(duration_s=duration),
        assertion_results=[_assertion(True)],
        metrics=[_metric()],
    )


def _failing_assert(
    suite: str = "my_suite",
    case: str = "my_case",
    duration: float = 0.3,
    message: str = "greet was not called",
) -> CaseResult:
    return CaseResult(
        suite_name=suite,
        case_name=case,
        trace=_trace(duration_s=duration),
        assertion_results=[_assertion(False, name="tool_called", message=message)],
    )


def _runner_error(
    suite: str = "my_suite",
    case: str = "error_case",
    error: str = "fixture not found: example.yaml",
) -> CaseResult:
    return CaseResult(
        suite_name=suite,
        case_name=case,
        trace=_trace(),
        assertion_results=[],
        error=error,
    )


def _agent_error(
    suite: str = "my_suite",
    case: str = "agent_error_case",
) -> CaseResult:
    return CaseResult(
        suite_name=suite,
        case_name=case,
        trace=_trace(exit_code=1, agent_error="agent crashed"),
        assertion_results=[],
    )


def _parse_junit(xml_str: str) -> ET.Element:
    """Parse a JUnit XML string (including XML declaration) into an Element.

    Encodes to bytes first so the XML parser can honour the encoding declaration.
    """
    return ET.fromstring(xml_str.encode("utf-8"))


# ---------------------------------------------------------------------------
# JUnit XML exporter
# ---------------------------------------------------------------------------


class TestJUnitExporter:
    exporter = JUnitExporter()

    def test_xml_declaration_present(self) -> None:
        assert self.exporter.export([]).startswith("<?xml")

    def test_empty_results_valid_xml(self) -> None:
        root = _parse_junit(self.exporter.export([]))
        assert root.tag == "testsuites"

    def test_empty_results_counts(self) -> None:
        root = _parse_junit(self.exporter.export([]))
        assert root.get("tests") == "0"
        assert root.get("failures") == "0"
        assert root.get("errors") == "0"

    def test_single_passing_structure(self) -> None:
        root = _parse_junit(self.exporter.export([_passing()]))
        suites = root.findall("testsuite")
        assert len(suites) == 1
        assert len(suites[0].findall("testcase")) == 1

    def test_multiple_suites(self) -> None:
        results = [_passing(suite="suite_a"), _passing(suite="suite_b")]
        root = _parse_junit(self.exporter.export(results))
        suite_names = {s.get("name") for s in root.findall("testsuite")}
        assert suite_names == {"suite_a", "suite_b"}

    def test_cases_grouped_by_suite(self) -> None:
        results = [
            _passing(suite="s", case="c1"),
            _passing(suite="s", case="c2"),
            _failing_assert(suite="s", case="c3"),
        ]
        root = _parse_junit(self.exporter.export(results))
        suites = root.findall("testsuite")
        assert len(suites) == 1
        assert suites[0].get("tests") == "3"

    def test_testcase_classname_and_name(self) -> None:
        root = _parse_junit(self.exporter.export([_passing(suite="s", case="c")]))
        tc = root.find(".//testcase")
        assert tc is not None
        assert tc.get("classname") == "s"
        assert tc.get("name") == "c"

    def test_testcase_time_attribute(self) -> None:
        root = _parse_junit(self.exporter.export([_passing(duration=1.234)]))
        tc = root.find(".//testcase")
        assert tc is not None
        assert tc.get("time") == "1.234"

    def test_zero_duration(self) -> None:
        root = _parse_junit(self.exporter.export([_passing(duration=0.0)]))
        tc = root.find(".//testcase")
        assert tc is not None
        assert tc.get("time") == "0.000"

    def test_total_time_attribute(self) -> None:
        results = [_passing(duration=0.5), _passing(case="c2", duration=1.0)]
        root = _parse_junit(self.exporter.export(results))
        assert float(root.get("time")) == pytest.approx(1.5, abs=0.001)

    def test_no_failure_for_passing_case(self) -> None:
        root = _parse_junit(self.exporter.export([_passing()]))
        tc = root.find(".//testcase")
        assert tc is not None
        assert tc.find("failure") is None
        assert tc.find("error") is None

    def test_failure_element_for_assertion_failure(self) -> None:
        root = _parse_junit(self.exporter.export([_failing_assert()]))
        failure = root.find(".//failure")
        assert failure is not None
        assert failure.get("type") == "AssertionFailure"
        assert failure.get("message") == "greet was not called"

    def test_failure_element_text_contains_details(self) -> None:
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[
                AssertionResult(
                    passed=False,
                    name="tool_called",
                    message="greet not called",
                    details={"expected": "greet", "got": "none"},
                )
            ],
        )
        root = _parse_junit(self.exporter.export([r]))
        failure = root.find(".//failure")
        assert failure is not None
        assert failure.text is not None
        assert "expected" in failure.text

    def test_multiple_assertion_failures_each_get_element(self) -> None:
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[
                _assertion(False, name="a1", message="fail1"),
                _assertion(False, name="a2", message="fail2"),
                _assertion(True, name="a3"),
            ],
        )
        root = _parse_junit(self.exporter.export([r]))
        failures = root.findall(".//failure")
        assert len(failures) == 2
        assert {f.get("message") for f in failures} == {"fail1", "fail2"}

    def test_error_element_for_runner_error(self) -> None:
        root = _parse_junit(self.exporter.export([_runner_error()]))
        error = root.find(".//error")
        assert error is not None
        assert error.get("type") == "RunnerError"
        assert "fixture not found" in (error.get("message") or "")

    def test_error_element_for_agent_failure(self) -> None:
        root = _parse_junit(self.exporter.export([_agent_error()]))
        error = root.find(".//error")
        assert error is not None
        assert error.get("type") == "AgentError"
        assert "agent crashed" in (error.get("message") or "")

    def test_failure_count_attribute(self) -> None:
        results = [_passing(), _failing_assert(case="c1"), _failing_assert(case="c2")]
        root = _parse_junit(self.exporter.export(results))
        assert root.get("failures") == "2"
        assert root.get("errors") == "0"

    def test_error_count_attribute(self) -> None:
        results = [_passing(), _runner_error(), _agent_error()]
        root = _parse_junit(self.exporter.export(results))
        assert root.get("errors") == "2"
        assert root.get("failures") == "0"

    def test_suite_level_failure_count(self) -> None:
        results = [_passing(suite="s"), _failing_assert(suite="s", case="c2")]
        root = _parse_junit(self.exporter.export(results))
        suite = root.find(".//testsuite[@name='s']")
        assert suite is not None
        assert suite.get("failures") == "1"
        assert suite.get("errors") == "0"

    def test_suite_level_error_count(self) -> None:
        results = [_passing(suite="s"), _runner_error(suite="s")]
        root = _parse_junit(self.exporter.export(results))
        suite = root.find(".//testsuite[@name='s']")
        assert suite is not None
        assert suite.get("errors") == "1"
        assert suite.get("failures") == "0"

    def test_properties_with_trace_id(self) -> None:
        t = _trace(trace_id="deadbeef1234")
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=t,
            assertion_results=[_assertion(True)],
        )
        root = _parse_junit(self.exporter.export([r]))
        props = root.findall(".//property")
        trace_prop = next(
            (p for p in props if p.get("name") == "mcptest.trace_id"), None
        )
        assert trace_prop is not None
        assert trace_prop.get("value") == "deadbeef1234"

    def test_properties_with_metric_scores(self) -> None:
        r = _passing()  # includes tool_efficiency=0.9
        root = _parse_junit(self.exporter.export([r]))
        props = root.findall(".//property")
        metric_prop = next(
            (p for p in props if p.get("name") == "mcptest.metric.tool_efficiency"),
            None,
        )
        assert metric_prop is not None
        assert float(metric_prop.get("value")) == pytest.approx(0.9, abs=1e-3)

    def test_unicode_in_suite_and_case_names(self) -> None:
        r = CaseResult(
            suite_name="测试套件",
            case_name="тест кейс",
            trace=_trace(),
            assertion_results=[_assertion(True)],
        )
        xml_str = self.exporter.export([r])
        assert "测试套件" in xml_str
        assert "тест кейс" in xml_str
        root = _parse_junit(xml_str)
        tc = root.find(".//testcase")
        assert tc is not None
        assert tc.get("classname") == "测试套件"
        assert tc.get("name") == "тест кейс"

    def test_testsuites_total_test_count(self) -> None:
        results = [_passing(), _failing_assert(case="c2"), _runner_error()]
        root = _parse_junit(self.exporter.export(results))
        assert root.get("tests") == "3"

    def test_no_properties_element_when_no_trace_id_and_no_metrics(self) -> None:
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(trace_id=""),
            assertion_results=[_assertion(True)],
            metrics=[],
        )
        root = _parse_junit(self.exporter.export([r]))
        assert root.find(".//properties") is None


# ---------------------------------------------------------------------------
# TAP v14 exporter
# ---------------------------------------------------------------------------


class TestTAPExporter:
    exporter = TAPExporter()

    def test_empty_results_header(self) -> None:
        lines = self.exporter.export([]).splitlines()
        assert lines[0] == "TAP version 14"
        assert lines[1] == "1..0"

    def test_ends_with_newline(self) -> None:
        assert self.exporter.export([]).endswith("\n")
        assert self.exporter.export([_passing()]).endswith("\n")

    def test_single_passing_ok_line(self) -> None:
        lines = self.exporter.export([_passing()]).splitlines()
        assert lines[0] == "TAP version 14"
        assert lines[1] == "1..1"
        assert lines[2].startswith("ok 1 - my_suite::my_case")

    def test_passing_includes_time(self) -> None:
        tap = self.exporter.export([_passing(duration=0.123)])
        assert "time=0.123s" in tap

    def test_failing_not_ok_line(self) -> None:
        lines = self.exporter.export([_failing_assert()]).splitlines()
        assert lines[2].startswith("not ok 1 - my_suite::my_case")

    def test_yaml_block_present_for_failure(self) -> None:
        tap = self.exporter.export([_failing_assert()])
        assert "  ---" in tap
        assert "  ..." in tap

    def test_yaml_message_for_assertion_failure(self) -> None:
        tap = self.exporter.export([_failing_assert(message="the tool was not called")])
        assert "the tool was not called" in tap

    def test_yaml_severity_fail_for_assertion(self) -> None:
        tap = self.exporter.export([_failing_assert()])
        assert "severity: fail" in tap

    def test_yaml_severity_error_for_runner_error(self) -> None:
        tap = self.exporter.export([_runner_error()])
        assert "severity: error" in tap

    def test_yaml_severity_error_for_agent_failure(self) -> None:
        tap = self.exporter.export([_agent_error()])
        assert "severity: error" in tap
        assert "agent crashed" in tap

    def test_yaml_duration_in_diagnostic(self) -> None:
        tap = self.exporter.export([_failing_assert()])
        assert "duration_s:" in tap

    def test_yaml_trace_id_in_diagnostic(self) -> None:
        t = _trace(trace_id="cafebabe1234")
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=t,
            assertion_results=[_assertion(False, message="x")],
        )
        tap = self.exporter.export([r])
        assert "trace_id: cafebabe1234" in tap

    def test_yaml_metrics_in_diagnostic(self) -> None:
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(),
            assertion_results=[_assertion(False, message="x")],
            metrics=[_metric("tool_efficiency", 0.75)],
        )
        tap = self.exporter.export([r])
        assert "metrics:" in tap
        assert "tool_efficiency:" in tap

    def test_no_yaml_block_for_passing(self) -> None:
        tap = self.exporter.export([_passing()])
        assert "---" not in tap

    def test_correct_numbering_mixed(self) -> None:
        results = [
            _passing(case="c1"),
            _failing_assert(case="c2"),
            _passing(case="c3"),
        ]
        tap = self.exporter.export(results)
        assert "ok 1 - " in tap
        assert "not ok 2 - " in tap
        assert "ok 3 - " in tap

    def test_plan_line_reflects_count(self) -> None:
        results = [_passing(case=f"c{i}") for i in range(10)]
        lines = self.exporter.export(results).splitlines()
        assert lines[1] == "1..10"

    def test_last_item_has_correct_number(self) -> None:
        results = [_passing(case=f"c{i}") for i in range(50)]
        lines = self.exporter.export(results).splitlines()
        assert lines[1] == "1..50"
        assert lines[-1].startswith("ok 50 - ")

    def test_unicode_test_names(self) -> None:
        r = CaseResult(
            suite_name="테스트",
            case_name="случай",
            trace=_trace(),
            assertion_results=[_assertion(True)],
        )
        tap = self.exporter.export([r])
        assert "테스트::случай" in tap

    def test_no_trace_id_when_empty(self) -> None:
        r = CaseResult(
            suite_name="s",
            case_name="c",
            trace=_trace(trace_id=""),
            assertion_results=[_assertion(False, message="x")],
        )
        tap = self.exporter.export([r])
        assert "trace_id:" not in tap

    def test_suite_case_separator_format(self) -> None:
        r = _passing(suite="my_suite", case="my_case")
        tap = self.exporter.export([r])
        assert "my_suite::my_case" in tap


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestExporterRegistry:
    def test_junit_registered(self) -> None:
        assert "junit" in EXPORTERS

    def test_tap_registered(self) -> None:
        assert "tap" in EXPORTERS

    def test_get_exporter_returns_junit_instance(self) -> None:
        assert isinstance(get_exporter("junit"), JUnitExporter)

    def test_get_exporter_returns_tap_instance(self) -> None:
        assert isinstance(get_exporter("tap"), TAPExporter)

    def test_get_exporter_unknown_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown exporter"):
            get_exporter("csv")

    def test_get_exporter_error_message_lists_known(self) -> None:
        with pytest.raises(ValueError, match="junit"):
            get_exporter("bogus")

    def test_each_call_returns_fresh_instance(self) -> None:
        assert get_exporter("junit") is not get_exporter("junit")


# ---------------------------------------------------------------------------
# CLI integration — `mcptest run --format <fmt>`
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


def _write_failing_project(tmp_path: Path) -> Path:
    _write_project(tmp_path)
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
        "      - tool_called: nonexistent_tool\n"
    )
    return tmp_path


class TestCLIFormatFlag:
    def test_format_junit_produces_valid_xml(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--format", "junit"]
        )
        assert result.exit_code == 0, result.output
        root = _parse_junit(result.output)
        assert root.tag == "testsuites"
        assert root.get("tests") == "1"

    def test_format_junit_passing_test_no_failure_elements(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--format", "junit"]
        )
        root = _parse_junit(result.output)
        assert root.get("failures") == "0"
        assert root.find(".//failure") is None

    def test_format_junit_failing_test_has_failure_element(self, tmp_path: Path) -> None:
        _write_failing_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--format", "junit"]
        )
        root = _parse_junit(result.output)
        assert root.get("failures") == "1"
        assert root.find(".//failure") is not None

    def test_format_tap_produces_valid_tap(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--format", "tap"]
        )
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert lines[0] == "TAP version 14"
        assert lines[1] == "1..1"
        assert lines[2].startswith("ok 1 -")

    def test_format_tap_failing_test_not_ok(self, tmp_path: Path) -> None:
        _write_failing_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["run", str(tmp_path / "tests"), "--format", "tap"]
        )
        assert "not ok 1 -" in result.output

    def test_format_json_equivalent_to_json_flag(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        r1 = runner.invoke(main, ["run", str(tmp_path / "tests"), "--json"])
        r2 = runner.invoke(main, ["run", str(tmp_path / "tests"), "--format", "json"])
        assert r1.exit_code == 0
        assert r2.exit_code == 0
        p1 = json.loads(r1.output)
        p2 = json.loads(r2.output)
        assert p1["passed"] == p2["passed"]
        assert p1["failed"] == p2["failed"]
        assert p1["total"] == p2["total"]

    def test_format_table_is_default(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["run", str(tmp_path / "tests")])
        assert result.exit_code == 0
        # Table format contains PASS/FAIL labels, not XML or TAP headers
        assert "PASS" in result.output
        assert "<?xml" not in result.output
        assert "TAP version" not in result.output


# ---------------------------------------------------------------------------
# CLI integration — `mcptest export`
# ---------------------------------------------------------------------------


class TestExportCommand:
    def _write_results_json(
        self, tmp_path: Path, results: list[CaseResult]
    ) -> Path:
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

    def test_export_to_junit(self, tmp_path: Path) -> None:
        results = [_passing(suite="s", case="c1"), _failing_assert(suite="s", case="c2")]
        json_file = self._write_results_json(tmp_path, results)
        runner = CliRunner()
        result = runner.invoke(main, ["export", str(json_file), "--format", "junit"])
        assert result.exit_code == 0
        root = _parse_junit(result.output)
        assert root.get("tests") == "2"
        assert root.get("failures") == "1"

    def test_export_to_tap(self, tmp_path: Path) -> None:
        results = [_passing(), _runner_error()]
        json_file = self._write_results_json(tmp_path, results)
        runner = CliRunner()
        result = runner.invoke(main, ["export", str(json_file), "--format", "tap"])
        assert result.exit_code == 0
        lines = result.output.splitlines()
        assert lines[0] == "TAP version 14"
        assert lines[1] == "1..2"

    def test_export_empty_cases(self, tmp_path: Path) -> None:
        json_file = self._write_results_json(tmp_path, [])
        runner = CliRunner()
        result = runner.invoke(main, ["export", str(json_file), "--format", "junit"])
        assert result.exit_code == 0
        root = _parse_junit(result.output)
        assert root.get("tests") == "0"

    def test_export_bad_json_exits_nonzero(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json at all {{ }}")
        runner = CliRunner()
        result = runner.invoke(main, ["export", str(bad_file), "--format", "junit"])
        assert result.exit_code != 0

    def test_export_format_option_required(self, tmp_path: Path) -> None:
        json_file = self._write_results_json(tmp_path, [])
        runner = CliRunner()
        result = runner.invoke(main, ["export", str(json_file)])
        # Missing required --format option
        assert result.exit_code != 0

    def test_export_roundtrip_preserves_counts(self, tmp_path: Path) -> None:
        """JSON → export junit → parse back → totals match original."""
        results = [
            _passing(suite="s", case="c1"),
            _passing(suite="s", case="c2"),
            _failing_assert(suite="s", case="c3"),
            _runner_error(suite="s"),
        ]
        json_file = self._write_results_json(tmp_path, results)
        runner = CliRunner()
        result = runner.invoke(main, ["export", str(json_file), "--format", "junit"])
        root = _parse_junit(result.output)
        assert root.get("tests") == "4"
        assert root.get("failures") == "1"
        assert root.get("errors") == "1"

    def test_export_tap_not_ok_preserved(self, tmp_path: Path) -> None:
        """A failing case in the JSON roundtrips to not-ok in TAP."""
        results = [_passing(case="c1"), _failing_assert(case="c2")]
        json_file = self._write_results_json(tmp_path, results)
        runner = CliRunner()
        result = runner.invoke(main, ["export", str(json_file), "--format", "tap"])
        assert "ok 1 -" in result.output
        assert "not ok 2 -" in result.output
