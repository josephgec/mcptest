"""Tests for mcptest.coverage — fixture surface area coverage analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mcptest.cli.main import main
from mcptest.coverage import (
    CoverageReport,
    ErrorCoverageItem,
    ResponseCoverageItem,
    ToolCoverageItem,
    analyze_coverage,
)
from mcptest.fixtures.models import ErrorSpec, Fixture, Response, ServerSpec, ToolSpec
from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner.trace import Trace
from mcptest.testspec.models import TestCase as SpecTestCase


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _server() -> ServerSpec:
    return ServerSpec(name="test-server")


def _response(
    *,
    return_value: dict | None = None,
    default: bool = False,
    match: dict | None = None,
    match_regex: dict | None = None,
    error: str | None = None,
) -> Response:
    if error is not None:
        return Response(default=default, match=match, match_regex=match_regex, error=error)
    kw: dict = {"return": return_value or {"ok": True}}
    if default:
        kw["default"] = True
    if match:
        kw["match"] = match
    if match_regex:
        kw["match_regex"] = match_regex
    return Response(**kw)


def _tool(name: str, responses: list[Response] | None = None) -> ToolSpec:
    return ToolSpec(
        name=name,
        responses=responses or [_response()],
    )


def _fixture(
    tools: list[ToolSpec] | None = None,
    errors: list[ErrorSpec] | None = None,
) -> Fixture:
    return Fixture(
        server=_server(),
        tools=tools or [],
        errors=errors or [],
    )


def _call(
    tool: str,
    arguments: dict | None = None,
    *,
    error: str | None = None,
) -> RecordedCall:
    return RecordedCall(
        tool=tool,
        arguments=arguments or {},
        result=None if error else {"ok": True},
        error=error,
    )


def _trace(calls: list[RecordedCall] | None = None) -> Trace:
    return Trace(tool_calls=calls or [])


def _case(name: str, inject_error: str | None = None) -> SpecTestCase:
    return SpecTestCase(name=name, assertions=[], inject_error=inject_error)


# ---------------------------------------------------------------------------
# TestResponseCoverageItem
# ---------------------------------------------------------------------------


class TestResponseCoverageItem:
    def test_frozen(self) -> None:
        r = ResponseCoverageItem(index=0, hit_count=2, is_default=False, match=None, match_regex=None, hit=True)
        with pytest.raises((AttributeError, TypeError)):
            r.hit_count = 3  # type: ignore[misc]

    def test_fields(self) -> None:
        r = ResponseCoverageItem(
            index=1,
            hit_count=5,
            is_default=True,
            match={"repo": "acme"},
            match_regex={"q": ".*"},
            hit=True,
        )
        assert r.index == 1
        assert r.hit_count == 5
        assert r.is_default is True
        assert r.match == {"repo": "acme"}
        assert r.match_regex == {"q": ".*"}
        assert r.hit is True

    def test_hit_false_when_count_zero(self) -> None:
        r = ResponseCoverageItem(index=0, hit_count=0, is_default=False, match=None, match_regex=None, hit=False)
        assert r.hit is False
        assert r.hit_count == 0


# ---------------------------------------------------------------------------
# TestToolCoverageItem
# ---------------------------------------------------------------------------


class TestToolCoverageItem:
    def test_frozen(self) -> None:
        t = ToolCoverageItem(name="x", call_count=0, responses_total=1, responses_hit=0, response_details=())
        with pytest.raises((AttributeError, TypeError)):
            t.call_count = 1  # type: ignore[misc]

    def test_fields(self) -> None:
        r = ResponseCoverageItem(index=0, hit_count=3, is_default=False, match=None, match_regex=None, hit=True)
        t = ToolCoverageItem(
            name="search",
            call_count=3,
            responses_total=2,
            responses_hit=1,
            response_details=(r,),
        )
        assert t.name == "search"
        assert t.call_count == 3
        assert t.responses_total == 2
        assert t.responses_hit == 1
        assert len(t.response_details) == 1


# ---------------------------------------------------------------------------
# TestErrorCoverageItem
# ---------------------------------------------------------------------------


class TestErrorCoverageItem:
    def test_frozen(self) -> None:
        e = ErrorCoverageItem(name="err", tool=None, injected=False, injection_count=0)
        with pytest.raises((AttributeError, TypeError)):
            e.injected = True  # type: ignore[misc]

    def test_fields(self) -> None:
        e = ErrorCoverageItem(name="rate_limited", tool="create_issue", injected=True, injection_count=2)
        assert e.name == "rate_limited"
        assert e.tool == "create_issue"
        assert e.injected is True
        assert e.injection_count == 2

    def test_no_tool_scope(self) -> None:
        e = ErrorCoverageItem(name="err", tool=None, injected=False, injection_count=0)
        assert e.tool is None


# ---------------------------------------------------------------------------
# TestCoverageReport
# ---------------------------------------------------------------------------


class TestCoverageReport:
    def _minimal_report(self) -> CoverageReport:
        r = ResponseCoverageItem(index=0, hit_count=1, is_default=False, match=None, match_regex=None, hit=True)
        t = ToolCoverageItem(name="get", call_count=1, responses_total=1, responses_hit=1, response_details=(r,))
        return CoverageReport(
            tools_total=1,
            tools_used=1,
            tool_details=(t,),
            responses_total=1,
            responses_hit=1,
            errors_total=0,
            errors_injected=0,
            error_details=(),
            overall_score=1.0,
            uncovered_summary=(),
        )

    def test_frozen(self) -> None:
        rep = self._minimal_report()
        with pytest.raises((AttributeError, TypeError)):
            rep.overall_score = 0.5  # type: ignore[misc]

    def test_to_dict_keys(self) -> None:
        rep = self._minimal_report()
        d = rep.to_dict()
        for key in (
            "tools_total", "tools_used", "tool_score",
            "responses_total", "responses_hit", "response_score",
            "errors_total", "errors_injected", "error_score",
            "overall_score", "tool_details", "error_details", "uncovered_summary",
        ):
            assert key in d, f"missing key: {key}"

    def test_to_dict_scores(self) -> None:
        rep = self._minimal_report()
        d = rep.to_dict()
        assert d["tool_score"] == 1.0
        assert d["response_score"] == 1.0
        assert d["error_score"] == 1.0
        assert d["overall_score"] == 1.0

    def test_to_dict_tool_detail_structure(self) -> None:
        rep = self._minimal_report()
        td = rep.to_dict()["tool_details"][0]
        assert td["name"] == "get"
        assert td["call_count"] == 1
        assert td["responses_total"] == 1
        assert td["responses_hit"] == 1
        assert len(td["responses"]) == 1
        resp = td["responses"][0]
        assert resp["index"] == 0
        assert resp["hit"] is True

    def test_to_text_contains_tool_name(self) -> None:
        rep = self._minimal_report()
        text = rep.to_text()
        assert "get" in text

    def test_to_text_contains_score(self) -> None:
        rep = self._minimal_report()
        text = rep.to_text()
        assert "100%" in text

    def test_to_text_suggestions_section(self) -> None:
        r = ResponseCoverageItem(index=0, hit_count=0, is_default=False, match=None, match_regex=None, hit=False)
        t = ToolCoverageItem(name="get", call_count=0, responses_total=1, responses_hit=0, response_details=(r,))
        rep = CoverageReport(
            tools_total=1, tools_used=0, tool_details=(t,),
            responses_total=1, responses_hit=0,
            errors_total=0, errors_injected=0, error_details=(),
            overall_score=0.0, uncovered_summary=("Use tool 'get'",),
        )
        text = rep.to_text()
        assert "Suggestions:" in text
        assert "Use tool 'get'" in text

    def test_to_text_error_table_present(self) -> None:
        e = ErrorCoverageItem(name="timeout", tool=None, injected=False, injection_count=0)
        r = ResponseCoverageItem(index=0, hit_count=1, is_default=False, match=None, match_regex=None, hit=True)
        t = ToolCoverageItem(name="get", call_count=1, responses_total=1, responses_hit=1, response_details=(r,))
        rep = CoverageReport(
            tools_total=1, tools_used=1, tool_details=(t,),
            responses_total=1, responses_hit=1,
            errors_total=1, errors_injected=0, error_details=(e,),
            overall_score=0.8, uncovered_summary=(),
        )
        text = rep.to_text()
        assert "Error Coverage" in text
        assert "timeout" in text

    def test_to_dict_error_score_zero_items(self) -> None:
        rep = self._minimal_report()
        d = rep.to_dict()
        # No errors → error_score defaults to 1.0
        assert d["error_score"] == 1.0

    def test_to_dict_roundtrip_json(self) -> None:
        rep = self._minimal_report()
        serialised = json.dumps(rep.to_dict())
        loaded = json.loads(serialised)
        assert loaded["overall_score"] == 1.0


# ---------------------------------------------------------------------------
# TestAnalyzeCoverage — core engine
# ---------------------------------------------------------------------------


class TestAnalyzeCoverage:
    """Tests for analyze_coverage()."""

    def test_empty_fixtures_and_traces(self) -> None:
        report = analyze_coverage([], [])
        assert report.tools_total == 0
        assert report.tools_used == 0
        assert report.responses_total == 0
        assert report.responses_hit == 0
        assert report.errors_total == 0
        assert report.errors_injected == 0
        assert report.overall_score == 1.0
        assert report.uncovered_summary == ()

    def test_no_traces(self) -> None:
        fx = _fixture(tools=[_tool("search")])
        report = analyze_coverage([fx], [])
        assert report.tools_total == 1
        assert report.tools_used == 0
        assert report.responses_total == 1
        assert report.responses_hit == 0
        assert report.overall_score < 1.0

    def test_all_tools_used_single_response(self) -> None:
        fx = _fixture(tools=[_tool("search"), _tool("fetch")])
        traces = [_trace([_call("search"), _call("fetch")])]
        report = analyze_coverage([fx], traces)
        assert report.tools_total == 2
        assert report.tools_used == 2
        assert report.responses_total == 2
        assert report.responses_hit == 2
        assert report.overall_score == 1.0
        assert report.uncovered_summary == ()

    def test_partial_tool_coverage(self) -> None:
        fx = _fixture(tools=[_tool("search"), _tool("fetch")])
        traces = [_trace([_call("search")])]
        report = analyze_coverage([fx], traces)
        assert report.tools_used == 1
        assert report.tools_total == 2
        # fetch was never called
        assert any("fetch" in s for s in report.uncovered_summary)

    def test_tool_call_not_in_fixture_is_ignored(self) -> None:
        fx = _fixture(tools=[_tool("search")])
        traces = [_trace([_call("unknown_tool"), _call("search")])]
        report = analyze_coverage([fx], traces)
        assert report.tools_total == 1
        assert report.tools_used == 1

    def test_response_matching_exact_match_hit(self) -> None:
        resp_match = _response(match={"repo": "acme"})
        resp_default = _response(default=True)
        tool = _tool("create_issue", [resp_match, resp_default])
        fx = _fixture(tools=[tool])

        # Call with matching args
        traces = [_trace([_call("create_issue", {"repo": "acme"})])]
        report = analyze_coverage([fx], traces)

        detail = report.tool_details[0]
        assert detail.response_details[0].hit is True   # match
        assert detail.response_details[1].hit is False  # default not hit

    def test_response_matching_default_hit(self) -> None:
        resp_match = _response(match={"repo": "acme"})
        resp_default = _response(default=True)
        tool = _tool("create_issue", [resp_match, resp_default])
        fx = _fixture(tools=[tool])

        # Call with non-matching args → falls through to default
        traces = [_trace([_call("create_issue", {"repo": "other"})])]
        report = analyze_coverage([fx], traces)

        detail = report.tool_details[0]
        assert detail.response_details[0].hit is False  # match not hit
        assert detail.response_details[1].hit is True   # default hit

    def test_response_matching_regex(self) -> None:
        resp_regex = _response(match_regex={"query": r"\d+"})
        resp_default = _response(default=True)
        tool = _tool("search", [resp_regex, resp_default])
        fx = _fixture(tools=[tool])

        traces = [_trace([_call("search", {"query": "123"})])]
        report = analyze_coverage([fx], traces)

        detail = report.tool_details[0]
        assert detail.response_details[0].hit is True
        assert detail.response_details[1].hit is False

    def test_response_no_match_raises_does_not_crash(self) -> None:
        """When no response matches (NoMatchError), the call is silently skipped."""
        resp_match = _response(match={"repo": "acme"})
        tool = _tool("create", [resp_match])
        fx = _fixture(tools=[tool])

        # Arguments that don't match — mock server would raise, but coverage skips
        traces = [_trace([_call("create", {"repo": "other"})])]
        report = analyze_coverage([fx], traces)
        assert report.responses_hit == 0

    def test_multiple_calls_same_response_hit_count(self) -> None:
        fx = _fixture(tools=[_tool("get")])
        traces = [_trace([_call("get"), _call("get"), _call("get")])]
        report = analyze_coverage([fx], traces)
        detail = report.tool_details[0]
        assert detail.call_count == 3
        assert detail.response_details[0].hit_count == 3

    def test_multiple_traces_accumulated(self) -> None:
        resp_a = _response(match={"k": "a"})
        resp_b = _response(match={"k": "b"})
        resp_def = _response(default=True)
        tool = _tool("q", [resp_a, resp_b, resp_def])
        fx = _fixture(tools=[tool])

        t1 = _trace([_call("q", {"k": "a"})])
        t2 = _trace([_call("q", {"k": "b"})])
        report = analyze_coverage([fx], [t1, t2])

        detail = report.tool_details[0]
        assert detail.response_details[0].hit is True
        assert detail.response_details[1].hit is True
        assert detail.response_details[2].hit is False
        assert detail.responses_hit == 2
        assert detail.call_count == 2

    def test_error_injection_tracking(self) -> None:
        err_spec = ErrorSpec(name="rate_limited", tool="create", message="rate limit")
        resp = _response(error="rate_limited")
        tool = _tool("create", [resp])
        fx = _fixture(tools=[tool], errors=[err_spec])

        cases = [_case("case1", inject_error="rate_limited"), _case("case2")]
        report = analyze_coverage([fx], [], test_cases=cases)

        assert report.errors_total == 1
        assert report.errors_injected == 1
        assert report.error_details[0].injected is True
        assert report.error_details[0].injection_count == 1

    def test_error_not_injected(self) -> None:
        err_spec = ErrorSpec(name="rate_limited", tool="create", message="rate limit")
        resp = _response(error="rate_limited")
        tool = _tool("create", [resp])
        fx = _fixture(tools=[tool], errors=[err_spec])

        report = analyze_coverage([fx], [])
        assert report.errors_total == 1
        assert report.errors_injected == 0
        assert report.error_details[0].injected is False

    def test_error_injection_count_multiple(self) -> None:
        err_spec = ErrorSpec(name="timeout", tool=None, message="timed out")
        tool = _tool("fetch", [_response(error="timeout")])
        fx = _fixture(tools=[tool], errors=[err_spec])

        cases = [
            _case("c1", inject_error="timeout"),
            _case("c2", inject_error="timeout"),
            _case("c3"),
        ]
        report = analyze_coverage([fx], [], test_cases=cases)
        assert report.error_details[0].injection_count == 2

    def test_unknown_inject_error_ignored(self) -> None:
        """inject_error referencing a non-existent error is silently ignored."""
        tool = _tool("get")
        fx = _fixture(tools=[tool])
        cases = [_case("c1", inject_error="nonexistent")]
        report = analyze_coverage([fx], [], test_cases=cases)
        assert report.errors_total == 0

    def test_overall_score_all_covered(self) -> None:
        err_spec = ErrorSpec(name="err", tool=None, message="e")
        tool = _tool("get", [_response(error="err")])
        fx = _fixture(tools=[tool], errors=[err_spec])
        traces = [_trace([_call("get")])]
        cases = [_case("c", inject_error="err")]
        report = analyze_coverage([fx], traces, test_cases=cases)
        # tool used ✓, response hit ✓ (error responses count), error injected ✓
        assert report.overall_score == 1.0

    def test_overall_score_no_errors_40_40_weight(self) -> None:
        """When there are no error specs, weight is redistributed to tool+response only."""
        tool = _tool("get")
        fx = _fixture(tools=[tool])
        # Tool called, response hit → tool=1.0 resp=1.0, no errors
        traces = [_trace([_call("get")])]
        report = analyze_coverage([fx], traces)
        assert report.overall_score == 1.0

    def test_overall_score_partial(self) -> None:
        t1 = _tool("a")
        t2 = _tool("b")
        fx = _fixture(tools=[t1, t2])
        traces = [_trace([_call("a")])]  # only 'a' called
        report = analyze_coverage([fx], traces)
        # tool_score = 0.5, resp_score = 0.5 (one response hit of two), error = none
        # With no errors: tool_score * 0.4 + resp_score * 0.4 / 0.8 total = 0.5
        assert report.overall_score == pytest.approx(0.5, abs=1e-5)

    def test_overall_score_zero_tools(self) -> None:
        """Fixtures with no tools → overall_score = 1.0 (nothing to miss)."""
        fx = _fixture()
        report = analyze_coverage([fx], [])
        assert report.overall_score == 1.0

    def test_multi_fixture_tool_deduplication(self) -> None:
        """When two fixtures declare the same tool, first one wins."""
        r1 = _response(match={"v": "1"})
        r2 = _response(default=True)
        t_v1 = _tool("get", [r1, r2])
        fx1 = _fixture(tools=[t_v1])

        # Second fixture also declares 'get' with different responses
        r3 = _response()
        t_v2 = _tool("get", [r3])
        fx2 = _fixture(tools=[t_v2])

        traces = [_trace([_call("get", {"v": "1"})])]
        report = analyze_coverage([fx1, fx2], traces)
        assert report.tools_total == 1
        detail = report.tool_details[0]
        # Response from fx1 (2 responses) should be used
        assert detail.responses_total == 2
        assert detail.response_details[0].hit is True

    def test_multi_fixture_error_deduplication(self) -> None:
        """Errors with the same name across fixtures are deduplicated."""
        err = ErrorSpec(name="e", tool=None, message="m")
        t1 = _tool("a", [_response(error="e")])
        fx1 = _fixture(tools=[t1], errors=[err])
        fx2 = _fixture(tools=[_tool("b")], errors=[err])
        report = analyze_coverage([fx1, fx2], [])
        assert report.errors_total == 1

    def test_uncovered_tool_in_suggestions(self) -> None:
        fx = _fixture(tools=[_tool("missing_tool")])
        report = analyze_coverage([fx], [])
        assert any("missing_tool" in s for s in report.uncovered_summary)

    def test_uncovered_response_in_suggestions(self) -> None:
        r_match = _response(match={"x": 1})
        r_def = _response(default=True)
        tool = _tool("t", [r_match, r_def])
        fx = _fixture(tools=[tool])
        # Only call with non-matching args → match response never hit
        traces = [_trace([_call("t", {"x": 99})])]
        report = analyze_coverage([fx], traces)
        assert any("response #0" in s for s in report.uncovered_summary)

    def test_uncovered_error_in_suggestions(self) -> None:
        err = ErrorSpec(name="boom", tool=None, message="b")
        tool = _tool("t", [_response(error="boom")])
        fx = _fixture(tools=[tool], errors=[err])
        report = analyze_coverage([fx], [])
        assert any("boom" in s for s in report.uncovered_summary)

    def test_response_coverage_item_describe_default(self) -> None:
        from mcptest.coverage.engine import _describe_response
        r = ResponseCoverageItem(index=0, hit_count=0, is_default=True, match=None, match_regex=None, hit=False)
        assert "default" in _describe_response(r)

    def test_response_coverage_item_describe_match(self) -> None:
        from mcptest.coverage.engine import _describe_response
        r = ResponseCoverageItem(index=1, hit_count=0, is_default=False, match={"k": "v"}, match_regex=None, hit=False)
        desc = _describe_response(r)
        assert "match" in desc
        assert "#1" in desc

    def test_response_coverage_item_describe_regex(self) -> None:
        from mcptest.coverage.engine import _describe_response
        r = ResponseCoverageItem(index=2, hit_count=0, is_default=False, match=None, match_regex={"q": r"\d+"}, hit=False)
        desc = _describe_response(r)
        assert "match_regex" in desc

    def test_response_coverage_item_describe_bare(self) -> None:
        from mcptest.coverage.engine import _describe_response
        r = ResponseCoverageItem(index=0, hit_count=0, is_default=False, match=None, match_regex=None, hit=False)
        assert "response #0" in _describe_response(r)

    def test_error_tool_scope_in_suggestions(self) -> None:
        err = ErrorSpec(name="forbidden", tool="delete", message="x")
        t = _tool("delete", [_response(error="forbidden")])
        fx = _fixture(tools=[t], errors=[err])
        report = analyze_coverage([fx], [])
        assert any("tool: delete" in s for s in report.uncovered_summary)

    def test_error_no_tool_scope_in_suggestions(self) -> None:
        err = ErrorSpec(name="global_err", tool=None, message="x")
        t = _tool("get", [_response(error="global_err")])
        fx = _fixture(tools=[t], errors=[err])
        report = analyze_coverage([fx], [])
        suggestion = next(s for s in report.uncovered_summary if "global_err" in s)
        assert "tool:" not in suggestion

    def test_no_test_cases_errors_zero_injection(self) -> None:
        err = ErrorSpec(name="e", tool=None, message="m")
        t = _tool("t", [_response(error="e")])
        fx = _fixture(tools=[t], errors=[err])
        report = analyze_coverage([fx], [], test_cases=None)
        assert report.errors_injected == 0

    def test_response_hit_count_zero_means_not_hit(self) -> None:
        r_a = _response(match={"a": 1})
        r_b = _response(default=True)
        tool = _tool("t", [r_a, r_b])
        fx = _fixture(tools=[tool])
        traces = [_trace([_call("t", {"a": 1}), _call("t", {"a": 1})])]
        report = analyze_coverage([fx], traces)
        detail = report.tool_details[0]
        assert detail.response_details[0].hit_count == 2
        assert detail.response_details[1].hit_count == 0
        assert detail.response_details[1].hit is False

    def test_to_dict_contains_response_match_info(self) -> None:
        r = _response(match={"repo": "acme"})
        tool = _tool("create", [r])
        fx = _fixture(tools=[tool])
        report = analyze_coverage([fx], [])
        td = report.to_dict()["tool_details"][0]["responses"][0]
        assert td["match"] == {"repo": "acme"}

    def test_empty_traces_list_no_crash(self) -> None:
        fx = _fixture(tools=[_tool("a"), _tool("b")])
        report = analyze_coverage([fx], [])
        assert report.tools_used == 0

    def test_error_only_fixture(self) -> None:
        """Fixture with errors but no tools."""
        err = ErrorSpec(name="e", tool=None, message="m")
        fx = _fixture(tools=[], errors=[err])
        report = analyze_coverage([fx], [])
        assert report.tools_total == 0
        assert report.errors_total == 1
        # Only error weight contributes → error_score=0, overall=0
        assert report.overall_score == 0.0


# ---------------------------------------------------------------------------
# TestCoverageCLI
# ---------------------------------------------------------------------------


def _write_fixture_yaml(path: Path, tools_yaml: str = "", errors_yaml: str = "") -> Path:
    """Write a minimal fixture YAML to *path* and return it."""
    content = "server:\n  name: test-server\n"
    if tools_yaml:
        content += f"tools:\n{tools_yaml}\n"
    else:
        content += "tools: []\n"
    if errors_yaml:
        content += f"errors:\n{errors_yaml}\n"
    path.write_text(content, encoding="utf-8")
    return path


def _write_trace_json(path: Path, calls: list[dict] | None = None) -> Path:
    trace = Trace(tool_calls=[RecordedCall.from_dict(c) for c in (calls or [])])
    path.write_text(trace.to_json(), encoding="utf-8")
    return path


def _write_run_json(path: Path, traces: list[Trace]) -> Path:
    """Write a full mcptest run output JSON (has a 'cases' key)."""
    payload = {
        "passed": len(traces),
        "failed": 0,
        "total": len(traces),
        "cases": [
            {
                "suite": "test",
                "case": f"case{i}",
                "passed": True,
                "error": None,
                "trace": t.to_dict(),
                "assertions": [],
                "metrics": [],
            }
            for i, t in enumerate(traces)
        ],
        "metric_summary": {},
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def _write_suite_yaml(path: Path, cases_yaml: str) -> Path:
    content = (
        "name: test-suite\n"
        "fixtures: []\n"
        "agent:\n  command: echo hi\n"
        f"cases:\n{cases_yaml}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


class TestCoverageCLI:
    """Integration tests for the `mcptest coverage` CLI command."""

    def test_requires_fixture(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["coverage"])
        assert result.exit_code == 1
        assert "fixture" in result.output.lower() or "fixture" in (result.output + (result.exception and str(result.exception) or "")).lower()

    def test_basic_table_output(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: search\n    responses:\n      - return: {ok: true}\n",
        )
        trace_path = _write_trace_json(
            tmp_path / "trace.json",
            calls=[{"tool": "search", "arguments": {}, "result": {"ok": True}, "error": None, "error_code": None, "latency_ms": 0, "server": "", "index": 0, "timestamp": 0.0}],
        )
        runner = CliRunner()
        result = runner.invoke(main, ["coverage", str(trace_path), "--fixture", str(fx_path)])
        assert result.exit_code == 0
        assert "search" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: get\n    responses:\n      - return: {ok: true}\n",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["coverage", "--fixture", str(fx_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "overall_score" in data
        assert "tool_details" in data
        assert "error_details" in data

    def test_json_output_no_trace(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: get\n    responses:\n      - return: {ok: true}\n",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["coverage", "--fixture", str(fx_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tools_total"] == 1
        assert data["tools_used"] == 0

    def test_threshold_pass(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: get\n    responses:\n      - return: {ok: true}\n",
        )
        trace_path = _write_trace_json(
            tmp_path / "trace.json",
            calls=[{"tool": "get", "arguments": {}, "result": {"ok": True}, "error": None, "error_code": None, "latency_ms": 0, "server": "", "index": 0, "timestamp": 0.0}],
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["coverage", str(trace_path), "--fixture", str(fx_path), "--threshold", "0.5"],
        )
        assert result.exit_code == 0

    def test_threshold_fail(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml=(
                "  - name: a\n    responses:\n      - return: {ok: true}\n"
                "  - name: b\n    responses:\n      - return: {ok: true}\n"
            ),
        )
        # Only call tool 'a' → coverage 50%
        trace_path = _write_trace_json(
            tmp_path / "trace.json",
            calls=[{"tool": "a", "arguments": {}, "result": {"ok": True}, "error": None, "error_code": None, "latency_ms": 0, "server": "", "index": 0, "timestamp": 0.0}],
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["coverage", str(trace_path), "--fixture", str(fx_path), "--threshold", "0.9"],
        )
        assert result.exit_code == 1

    def test_threshold_zero_never_fails(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: get\n    responses:\n      - return: {ok: true}\n",
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["coverage", "--fixture", str(fx_path), "--threshold", "0.0"]
        )
        assert result.exit_code == 0

    def test_full_run_json_input(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: search\n    responses:\n      - return: {ok: true}\n",
        )
        call = RecordedCall(tool="search", arguments={}, result={"ok": True})
        trace = Trace(tool_calls=[call])
        run_path = _write_run_json(tmp_path / "run.json", [trace])
        runner = CliRunner()
        result = runner.invoke(
            main, ["coverage", str(run_path), "--fixture", str(fx_path), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tools_used"] == 1

    def test_single_trace_json_input(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: get\n    responses:\n      - return: {ok: true}\n",
        )
        call = RecordedCall(tool="get", arguments={}, result={"ok": True})
        trace = Trace(tool_calls=[call])
        trace_path = tmp_path / "trace.json"
        trace.save(trace_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["coverage", str(trace_path), "--fixture", str(fx_path), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tools_used"] == 1

    def test_suite_inject_error_tracked(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: t\n    responses:\n      - error: boom\n",
            errors_yaml="  - name: boom\n    message: boom\n",
        )
        suite_path = _write_suite_yaml(
            tmp_path / "suite.yaml",
            cases_yaml="  - name: c1\n    inject_error: boom\n",
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["coverage", "--fixture", str(fx_path), "--suite", str(suite_path), "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["errors_injected"] == 1

    def test_invalid_fixture_path_errors(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["coverage", "--fixture", "/nonexistent/fix.yaml"])
        assert result.exit_code != 0

    def test_malformed_results_json_errors(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: get\n    responses:\n      - return: {ok: true}\n",
        )
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("not valid json", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            main, ["coverage", str(bad_json), "--fixture", str(fx_path)]
        )
        assert result.exit_code == 1

    def test_multiple_fixtures(self, tmp_path: Path) -> None:
        fx1 = _write_fixture_yaml(
            tmp_path / "f1.yaml",
            tools_yaml="  - name: tool_a\n    responses:\n      - return: {ok: true}\n",
        )
        fx2 = _write_fixture_yaml(
            tmp_path / "f2.yaml",
            tools_yaml="  - name: tool_b\n    responses:\n      - return: {ok: true}\n",
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["coverage", "--fixture", str(fx1), "--fixture", str(fx2), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tools_total"] == 2

    def test_coverage_command_in_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "coverage" in result.output

    def test_table_output_shows_overall_score(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: get\n    responses:\n      - return: {ok: true}\n",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["coverage", "--fixture", str(fx_path)])
        assert result.exit_code == 0
        assert "Overall coverage score" in result.output

    def test_error_coverage_in_json(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: t\n    responses:\n      - error: e\n",
            errors_yaml="  - name: e\n    message: oops\n",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["coverage", "--fixture", str(fx_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["errors_total"] == 1
        assert len(data["error_details"]) == 1
        assert data["error_details"][0]["name"] == "e"

    def test_uncovered_summary_in_json(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: unused\n    responses:\n      - return: {ok: true}\n",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["coverage", "--fixture", str(fx_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["uncovered_summary"]) > 0
        assert any("unused" in s for s in data["uncovered_summary"])

    def test_threshold_message_in_output(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: missing\n    responses:\n      - return: {ok: true}\n",
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["coverage", "--fixture", str(fx_path), "--threshold", "0.99"]
        )
        assert result.exit_code == 1
        assert "threshold" in result.output.lower() or "below" in result.output.lower()

    def test_invalid_suite_path_errors(self, tmp_path: Path) -> None:
        fx_path = _write_fixture_yaml(
            tmp_path / "fix.yaml",
            tools_yaml="  - name: get\n    responses:\n      - return: {ok: true}\n",
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["coverage", "--fixture", str(fx_path), "--suite", "/nonexistent/suite.yaml"],
        )
        assert result.exit_code != 0
