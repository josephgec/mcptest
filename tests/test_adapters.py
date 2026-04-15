"""Unit tests for agent adapters."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mcptest.runner.adapters import (
    AgentResult,
    CallableAdapter,
    PythonScriptAdapter,
    SubprocessAdapter,
)


class TestAgentResult:
    def test_defaults(self) -> None:
        r = AgentResult(output="hi")
        assert r.output == "hi"
        assert r.stderr == ""
        assert r.exit_code == 0
        assert r.error is None
        assert r.metadata == {}


class TestCallableAdapter:
    def test_string_return(self) -> None:
        def agent(inp: str, env: dict[str, str]) -> str:
            return f"echo: {inp}"

        result = CallableAdapter(agent).run("hello", {})
        assert result.output == "echo: hello"
        assert result.exit_code == 0
        assert result.duration_s >= 0

    def test_agent_result_return_preserves_fields(self) -> None:
        def agent(inp: str, env: dict[str, str]) -> AgentResult:
            return AgentResult(output="x", stderr="warn", exit_code=2)

        result = CallableAdapter(agent).run("hi", {})
        assert result.output == "x"
        assert result.stderr == "warn"
        assert result.exit_code == 2

    def test_agent_result_preserves_existing_duration(self) -> None:
        def agent(inp: str, env: dict[str, str]) -> AgentResult:
            return AgentResult(output="x", duration_s=42.0)

        result = CallableAdapter(agent).run("hi", {})
        assert result.duration_s == 42.0

    def test_env_passed_through(self) -> None:
        captured: dict[str, str] = {}

        def agent(inp: str, env: dict[str, str]) -> str:
            captured.update(env)
            return "ok"

        CallableAdapter(agent).run("", {"X": "1", "Y": "2"})
        assert captured["X"] == "1"
        assert captured["Y"] == "2"

    def test_exception_captured(self) -> None:
        def agent(inp: str, env: dict[str, str]) -> str:
            raise RuntimeError("nope")

        result = CallableAdapter(agent).run("", {})
        assert result.error is not None
        assert "RuntimeError" in result.error
        assert "nope" in result.error
        assert result.exit_code == -1


class TestSubprocessAdapter:
    def test_stdin_input(self) -> None:
        adapter = SubprocessAdapter(
            command=sys.executable,
            args=["-c", "import sys; print('echo:', sys.stdin.read().strip())"],
        )
        result = adapter.run("hello", {})
        assert result.exit_code == 0
        assert "echo: hello" in result.output

    def test_env_propagates(self) -> None:
        adapter = SubprocessAdapter(
            command=sys.executable,
            args=["-c", "import os; print(os.environ.get('MY_VAR', 'missing'))"],
            env={"MY_VAR": "from_adapter"},
        )
        result = adapter.run("", {})
        assert "from_adapter" in result.output

    def test_runner_env_overrides_adapter_env(self) -> None:
        adapter = SubprocessAdapter(
            command=sys.executable,
            args=["-c", "import os; print(os.environ['MY_VAR'])"],
            env={"MY_VAR": "adapter"},
        )
        result = adapter.run("", {"MY_VAR": "runner"})
        assert "runner" in result.output

    def test_nonzero_exit_captured(self) -> None:
        adapter = SubprocessAdapter(
            command=sys.executable,
            args=["-c", "import sys; sys.exit(7)"],
        )
        result = adapter.run("", {})
        assert result.exit_code == 7
        assert result.error is None

    def test_stderr_captured(self) -> None:
        adapter = SubprocessAdapter(
            command=sys.executable,
            args=["-c", "import sys; print('warn', file=sys.stderr)"],
        )
        result = adapter.run("", {})
        assert "warn" in result.stderr

    def test_timeout(self) -> None:
        adapter = SubprocessAdapter(
            command=sys.executable,
            args=["-c", "import time; time.sleep(5)"],
            timeout_s=0.3,
        )
        result = adapter.run("", {})
        assert result.error is not None
        assert "timed out" in result.error
        assert result.exit_code == -1

    def test_missing_command(self) -> None:
        adapter = SubprocessAdapter(command="this-command-does-not-exist-12345")
        result = adapter.run("", {})
        assert result.error is not None
        assert "not found" in result.error
        assert result.exit_code == -1

    def test_input_via_arg(self) -> None:
        adapter = SubprocessAdapter(
            command=sys.executable,
            args=["-c", "import sys; print('got:', sys.argv[1])"],
            input_via="arg",
        )
        result = adapter.run("hi", {})
        assert "got: hi" in result.output

    def test_input_via_env(self) -> None:
        adapter = SubprocessAdapter(
            command=sys.executable,
            args=["-c", "import os; print('got:', os.environ['AGENT_INPUT'])"],
            input_via="env:AGENT_INPUT",
        )
        result = adapter.run("hi", {})
        assert "got: hi" in result.output


class TestPythonScriptAdapter:
    def test_runs_script(self, tmp_path: Path) -> None:
        script = tmp_path / "hello.py"
        script.write_text("print('hello-from-script')\n")

        adapter = PythonScriptAdapter(str(script))
        result = adapter.run("", {})
        assert "hello-from-script" in result.output

    def test_passes_args(self, tmp_path: Path) -> None:
        script = tmp_path / "args.py"
        script.write_text("import sys; print(sys.argv[1:])\n")

        adapter = PythonScriptAdapter(str(script), args=["a", "b"])
        result = adapter.run("", {})
        assert "'a'" in result.output
        assert "'b'" in result.output

    def test_passes_env(self, tmp_path: Path) -> None:
        script = tmp_path / "env.py"
        script.write_text("import os; print(os.environ.get('X'))\n")

        adapter = PythonScriptAdapter(str(script), env={"X": "hi"})
        result = adapter.run("", {})
        assert "hi" in result.output
