"""End-to-end smoke tests — a real scripted agent talking real MCP to
a real mcptest mock server subprocess.

These tests verify that Sessions 1–5 compose: the runner exports env,
the scripted agent spawns `python -m mcptest.mock_server FIXTURE` over
real stdio, both sides speak MCP via the official SDK, and the resulting
trajectory shows up in the trace file that assertions evaluate against.

If anything in the protocol chain breaks (framing, initialization,
tool dispatch, result serialization) these tests will catch it at CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from mcptest.assertions import (
    check_all,
    max_tool_calls,
    no_errors,
    param_matches,
    tool_call_count,
    tool_called,
    tool_not_called,
    tool_order,
)
from mcptest.cli.main import main as cli_main
from mcptest.runner import PythonScriptAdapter, Runner


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTED_AGENT = REPO_ROOT / "examples" / "scripted_agent.py"
FIXTURE = REPO_ROOT / "examples" / "e2e_fixture.yaml"


def _make_runner() -> Runner:
    return Runner(
        fixtures=[str(FIXTURE)],
        agent=PythonScriptAdapter(str(SCRIPTED_AGENT), timeout_s=30),
    )


class TestScriptedAgentRoundTrip:
    def test_greet_world_tool_called(self) -> None:
        runner = _make_runner()
        trace = runner.run("greet world")
        assert trace.succeeded, f"agent failed: {trace.agent_error} / {trace.stderr}"
        assert trace.total_tool_calls == 1
        call = trace.tool_calls[0]
        assert call.tool == "greet"
        assert call.arguments == {"name": "world"}
        assert call.is_error is False
        assert "Hello, world!" in trace.output

    def test_list_issues(self) -> None:
        runner = _make_runner()
        trace = runner.run("list")
        assert trace.succeeded
        assert trace.total_tool_calls == 1
        assert trace.tool_calls[0].tool == "list_issues"
        assert "First bug" in trace.output

    def test_create_issue_matched_repo(self) -> None:
        runner = _make_runner()
        trace = runner.run("create acme/api fix login bug")
        assert trace.succeeded
        call = trace.tool_calls[0]
        assert call.tool == "create_issue"
        assert call.arguments["repo"] == "acme/api"
        assert "fix login bug" in call.arguments["title"]
        assert call.result == {
            "issue_number": 42,
            "url": "https://github.com/acme/api/issues/42",
        }

    def test_create_issue_fallback_triggers_error_response(self) -> None:
        runner = _make_runner()
        trace = runner.run("create other/repo something")
        assert trace.exit_code == 0  # error is tool-level, not protocol-level
        assert trace.total_tool_calls == 1
        call = trace.tool_calls[0]
        assert call.is_error is True
        assert call.error_code == -32000
        assert "rate limit" in (call.error or "").lower()

    def test_multi_command_trajectory(self) -> None:
        runner = _make_runner()
        trace = runner.run("greet world, list, farewell")
        assert trace.succeeded
        assert trace.tool_names == ["greet", "list_issues", "farewell"]
        assert trace.total_tool_calls == 3

    def test_unknown_input_produces_no_calls(self) -> None:
        runner = _make_runner()
        trace = runner.run("flargle bargle")
        assert trace.succeeded
        assert trace.total_tool_calls == 0
        assert "(no tool output)" in trace.output


class TestAssertionsAgainstRealTrace:
    def test_assertions_evaluate_correctly(self) -> None:
        runner = _make_runner()
        trace = runner.run("greet world, list")

        results = check_all(
            [
                tool_called("greet"),
                tool_called("list_issues"),
                tool_not_called("farewell"),
                tool_order(["greet", "list_issues"]),
                tool_call_count("greet", 1),
                max_tool_calls(5),
                param_matches(tool="greet", param="name", value="world"),
                no_errors(),
            ],
            trace,
        )
        assert all(r.passed for r in results), [
            (r.name, r.message) for r in results if not r.passed
        ]


class TestCliEndToEnd:
    def test_cli_run_on_real_agent(self, tmp_path: Path) -> None:
        # Write a tests/ directory referencing the real fixture and agent.
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_real.yaml"
        test_file.write_text(
            "name: real-smoke\n"
            f"fixtures:\n  - {FIXTURE}\n"
            "agent:\n"
            f"  command: {sys.executable} {SCRIPTED_AGENT}\n"
            "  timeout_s: 30\n"
            "cases:\n"
            "  - name: greet\n"
            "    input: greet world\n"
            "    assertions:\n"
            "      - tool_called: greet\n"
            "      - param_matches:\n"
            "          tool: greet\n"
            "          param: name\n"
            "          value: world\n"
            "      - max_tool_calls: 1\n"
            "      - no_errors: true\n"
            "  - name: handles rate limit error\n"
            "    input: create other/repo oops\n"
            "    assertions:\n"
            "      - tool_called: create_issue\n"
            "      - error_handled: rate limit\n"
        )

        runner = CliRunner()
        result = runner.invoke(
            cli_main, ["run", str(tests_dir), "--ci"]
        )
        assert result.exit_code == 0, result.output
        assert "PASS" in result.output
        assert "2 passed" in result.output
