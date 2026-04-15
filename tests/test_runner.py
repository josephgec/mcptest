"""Unit tests for the Runner class."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner import (
    AgentResult,
    CallableAdapter,
    Runner,
    RunnerError,
    SubprocessAdapter,
)
from mcptest.runner.runner import FIXTURES_ENV
from mcptest.mock_server.recorder import TRACE_FILE_ENV, TraceFileCallLog


def _write_fixture(tmp_path: Path, name: str = "x.yaml") -> Path:
    p = tmp_path / name
    p.write_text(
        "server: { name: mock-test }\n"
        "tools:\n"
        "  - name: ping\n"
        "    responses:\n"
        "      - return_text: pong\n"
    )
    return p


class TestRunnerConstruction:
    def test_requires_fixtures(self, tmp_path: Path) -> None:
        with pytest.raises(RunnerError, match="at least one fixture"):
            Runner(fixtures=[], agent=CallableAdapter(lambda i, e: "x"))

    def test_invalid_fixture_raises_immediately(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("not-a-mapping\n")
        from mcptest.fixtures.loader import FixtureLoadError

        with pytest.raises(FixtureLoadError):
            Runner(fixtures=[str(bad)], agent=CallableAdapter(lambda i, e: "x"))

    def test_loads_fixture_names(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        runner = Runner(
            fixtures=[str(p)],
            agent=CallableAdapter(lambda i, e: "x"),
        )
        assert len(runner.loaded_fixtures) == 1
        assert runner.loaded_fixtures[0].server.name == "mock-test"

    def test_context_manager(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        with Runner(
            fixtures=[str(p)], agent=CallableAdapter(lambda i, e: "x")
        ) as runner:
            assert isinstance(runner, Runner)


class TestRunWithCallableAgent:
    def test_records_output(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        runner = Runner(
            fixtures=[str(p)],
            agent=CallableAdapter(lambda inp, env: f"echo: {inp}"),
        )
        trace = runner.run("hello")
        assert trace.output == "echo: hello"
        assert trace.input == "hello"
        assert trace.exit_code == 0
        assert trace.succeeded is True

    def test_env_contains_trace_file_and_fixtures(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        captured: dict[str, str] = {}

        def agent(inp: str, env: dict[str, str]) -> str:
            captured.update(env)
            return "ok"

        runner = Runner(fixtures=[str(p)], agent=CallableAdapter(agent))
        runner.run("hi")

        assert TRACE_FILE_ENV in captured
        assert FIXTURES_ENV in captured
        fixtures = json.loads(captured[FIXTURES_ENV])
        assert len(fixtures) == 1
        assert fixtures[0].endswith("x.yaml")
        assert "MCPTEST_RUN_ID" in captured

    def test_agent_writing_trace_file_appears_in_trace(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)

        def agent(inp: str, env: dict[str, str]) -> str:
            log = TraceFileCallLog(env[TRACE_FILE_ENV])
            log.append(
                RecordedCall(
                    tool="ping",
                    arguments={},
                    result="pong",
                    server_name="mock-test",
                )
            )
            log.append(
                RecordedCall(
                    tool="other",
                    arguments={"x": 1},
                    result={"y": 2},
                    server_name="mock-test",
                )
            )
            return "done"

        runner = Runner(fixtures=[str(p)], agent=CallableAdapter(agent))
        trace = runner.run("run me")
        assert trace.total_tool_calls == 2
        assert trace.tool_names == ["ping", "other"]
        assert trace.calls_to("ping")[0].result == "pong"

    def test_exception_in_agent_recorded(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)

        def agent(inp: str, env: dict[str, str]) -> str:
            raise ValueError("boom")

        runner = Runner(fixtures=[str(p)], agent=CallableAdapter(agent))
        trace = runner.run("")
        assert trace.succeeded is False
        assert trace.agent_error is not None
        assert "ValueError" in trace.agent_error

    def test_run_many(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        runner = Runner(
            fixtures=[str(p)],
            agent=CallableAdapter(lambda inp, env: inp.upper()),
        )
        traces = runner.run_many(["a", "b", "c"])
        assert [t.output for t in traces] == ["A", "B", "C"]
        assert len({t.trace_id for t in traces}) == 3

    def test_extra_env(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        captured: dict[str, str] = {}

        def agent(inp: str, env: dict[str, str]) -> str:
            captured.update(env)
            return ""

        runner = Runner(
            fixtures=[str(p)],
            agent=CallableAdapter(agent),
            extra_env={"FOO": "bar"},
        )
        runner.run("")
        assert captured["FOO"] == "bar"

    def test_metadata_propagation(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        runner = Runner(fixtures=[str(p)], agent=CallableAdapter(lambda i, e: ""))
        trace = runner.run("", metadata={"branch": "main"})
        assert trace.metadata["branch"] == "main"
        assert "run_id" in trace.metadata
        assert trace.metadata["fixtures"] == ["mock-test"]

    def test_trace_file_cleaned_up_by_default(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        captured: dict[str, str] = {}

        def agent(inp: str, env: dict[str, str]) -> str:
            captured["trace_file"] = env[TRACE_FILE_ENV]
            return ""

        runner = Runner(
            fixtures=[str(p)],
            agent=CallableAdapter(agent),
            workdir=tmp_path / "work",
        )
        runner.run("")
        assert not Path(captured["trace_file"]).exists()

    def test_keep_traces_retains_file(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        captured: dict[str, str] = {}

        def agent(inp: str, env: dict[str, str]) -> str:
            captured["trace_file"] = env[TRACE_FILE_ENV]
            return ""

        runner = Runner(
            fixtures=[str(p)],
            agent=CallableAdapter(agent),
            workdir=tmp_path / "work",
            keep_traces=True,
        )
        runner.run("")
        assert Path(captured["trace_file"]).exists()


class TestRunWithSubprocessAgent:
    def test_subprocess_roundtrip_with_trace_file(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path)
        # A tiny inline Python agent that opens MCPTEST_TRACE_FILE and writes
        # one fake RecordedCall, then echoes the input.
        script = tmp_path / "fake_agent.py"
        script.write_text(
            "import os, json, sys, time\n"
            "trace = os.environ['MCPTEST_TRACE_FILE']\n"
            "fixtures = json.loads(os.environ['MCPTEST_FIXTURES'])\n"
            "with open(trace, 'a') as f:\n"
            "    f.write(json.dumps({\n"
            "        'index': 0, 'tool': 'ping', 'server': 'mock-test',\n"
            "        'arguments': {}, 'result': 'pong',\n"
            "        'error': None, 'error_code': None,\n"
            "        'latency_ms': 1.0, 'timestamp': time.time(),\n"
            "    }) + '\\n')\n"
            "print('agent saw', len(fixtures), 'fixtures')\n"
            "print('input was:', sys.stdin.read().strip())\n"
        )

        runner = Runner(
            fixtures=[str(p)],
            agent=SubprocessAdapter(
                command=sys.executable,
                args=[str(script)],
                timeout_s=10,
            ),
        )
        trace = runner.run("hi")
        assert trace.exit_code == 0
        assert "agent saw 1 fixtures" in trace.output
        assert "input was: hi" in trace.output
        assert trace.total_tool_calls == 1
        assert trace.tool_calls[0].tool == "ping"
