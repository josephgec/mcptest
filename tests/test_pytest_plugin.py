"""Tests for the mcptest pytest11 plugin.

We cover both the `@mcptest.mock` decorator + `mcptest_runner` fixture, and
the YAML test-file collection hook. Because the plugin itself runs inside
pytest, we use pytest's `pytester` fixture to spin up a nested pytest
session with a minimal test project each time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcptest.fixtures.models import (
    Fixture,
    Response,
    ServerSpec,
    ToolSpec,
)
from mcptest.pytest_plugin import _MockConfig, _resolve_agent, mock
from mcptest.runner import AgentResult, CallableAdapter, SubprocessAdapter


# ---------------------------------------------------------------------------
# `@mock` decorator + internal helpers — unit tests
# ---------------------------------------------------------------------------


class TestMockDecorator:
    def test_attaches_config(self) -> None:
        @mock("a.yaml", "b.yaml", agent="python x.py")
        def sample() -> None:
            pass

        cfg = sample._mcptest_config  # type: ignore[attr-defined]
        assert isinstance(cfg, _MockConfig)
        assert cfg.fixtures == ("a.yaml", "b.yaml")
        assert cfg.agent == "python x.py"

    def test_cwd_stored_as_path(self) -> None:
        @mock("x.yaml", agent="x", cwd="/tmp/work")
        def sample() -> None:
            pass

        cfg = sample._mcptest_config  # type: ignore[attr-defined]
        assert isinstance(cfg.cwd, Path)
        assert cfg.cwd == Path("/tmp/work")

    def test_default_cwd_none(self) -> None:
        @mock("x.yaml", agent="x")
        def sample() -> None:
            pass

        cfg = sample._mcptest_config  # type: ignore[attr-defined]
        assert cfg.cwd is None


class TestResolveAgent:
    def test_none_raises(self, tmp_path: Path) -> None:
        with pytest.raises(pytest.UsageError, match="requires an agent"):
            _resolve_agent(None, tmp_path)

    def test_string_builds_subprocess_adapter(self, tmp_path: Path) -> None:
        adapter = _resolve_agent("python foo.py", tmp_path)
        assert isinstance(adapter, SubprocessAdapter)

    def test_callable_wrapped(self, tmp_path: Path) -> None:
        def agent(inp: str, env: dict[str, str]) -> str:
            return "ok"

        adapter = _resolve_agent(agent, tmp_path)
        assert isinstance(adapter, CallableAdapter)

    def test_prebuilt_adapter_passthrough(self, tmp_path: Path) -> None:
        original = CallableAdapter(lambda i, e: "x")
        assert _resolve_agent(original, tmp_path) is original

    def test_prebuilt_subprocess_passthrough(self, tmp_path: Path) -> None:
        original = SubprocessAdapter(command="x")
        assert _resolve_agent(original, tmp_path) is original


# ---------------------------------------------------------------------------
# `mcptest_runner` fixture — nested pytest session
# ---------------------------------------------------------------------------


_FIXTURE_YAML = """\
server: { name: nested }
tools:
  - name: ping
    responses:
      - return_text: pong
"""


class TestMcptestRunnerFixture:
    def test_runner_fixture_runs_callable_agent(self, pytester: pytest.Pytester) -> None:
        (pytester.path / "fixtures").mkdir()
        (pytester.path / "fixtures" / "f.yaml").write_text(_FIXTURE_YAML)

        pytester.makepyfile(
            test_nested="""
            from mcptest import mock

            def _agent(inp, env):
                return f"echo: {inp}"

            @mock("fixtures/f.yaml", agent=_agent)
            def test_callable(mcptest_runner):
                trace = mcptest_runner.run("hello")
                assert trace.output == "echo: hello"
                assert trace.succeeded
            """
        )
        result = pytester.runpytest("-q")
        result.assert_outcomes(passed=1)

    def test_runner_fixture_runs_string_command(
        self, pytester: pytest.Pytester
    ) -> None:
        (pytester.path / "fixtures").mkdir()
        (pytester.path / "fixtures" / "f.yaml").write_text(_FIXTURE_YAML)

        pytester.makepyfile(
            test_nested="""
            import sys
            from mcptest import mock

            @mock("fixtures/f.yaml", agent=f"{sys.executable} -c 'print(\\"ran\\")'")
            def test_string(mcptest_runner):
                trace = mcptest_runner.run("")
                assert "ran" in trace.output
            """
        )
        result = pytester.runpytest("-q")
        result.assert_outcomes(passed=1)

    def test_without_mock_decorator_raises(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            test_nested="""
            def test_no_decorator(mcptest_runner):
                pass
            """
        )
        result = pytester.runpytest("-q")
        result.assert_outcomes(errors=1)

    def test_trace_fixture_returns_empty_trace(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makepyfile(
            test_nested="""
            def test_trace(mcptest_trace):
                assert mcptest_trace.total_tool_calls == 0
                assert mcptest_trace.input == ""
            """
        )
        result = pytester.runpytest("-q")
        result.assert_outcomes(passed=1)


# ---------------------------------------------------------------------------
# YAML test-file collection hook
# ---------------------------------------------------------------------------


_REAL_AGENT = """\
import json, os, sys, time
trace = os.environ['MCPTEST_TRACE_FILE']
inp = sys.stdin.read().strip()
with open(trace, 'a') as f:
    f.write(json.dumps({
        'tool': 'greet', 'server': 'nested',
        'arguments': {'name': inp or 'world'},
        'result': {'ok': True}, 'error': None, 'error_code': None,
        'latency_ms': 1.0, 'timestamp': time.time(),
    }) + '\\n')
print('done')
"""


class TestYamlCollection:
    def test_yaml_file_collected_and_passes(self, pytester: pytest.Pytester) -> None:
        (pytester.path / "fixtures").mkdir()
        (pytester.path / "fixtures" / "f.yaml").write_text(
            "server: { name: nested }\n"
            "tools:\n"
            "  - name: greet\n"
            "    responses:\n"
            "      - return: { ok: true }\n"
        )
        (pytester.path / "agent.py").write_text(_REAL_AGENT)

        import sys as _sys

        (pytester.path / "test_case.yaml").write_text(
            "name: yaml-suite\n"
            "fixtures:\n  - fixtures/f.yaml\n"
            "agent:\n"
            f"  command: {_sys.executable} agent.py\n"
            "  timeout_s: 10\n"
            "cases:\n"
            "  - name: greet world\n"
            "    input: world\n"
            "    assertions:\n"
            "      - tool_called: greet\n"
        )
        result = pytester.runpytest("-q")
        result.assert_outcomes(passed=1)

    def test_yaml_file_failing_assertion(self, pytester: pytest.Pytester) -> None:
        (pytester.path / "fixtures").mkdir()
        (pytester.path / "fixtures" / "f.yaml").write_text(
            "server: { name: nested }\n"
            "tools:\n"
            "  - name: greet\n"
            "    responses:\n"
            "      - return: { ok: true }\n"
        )
        (pytester.path / "agent.py").write_text(_REAL_AGENT)

        import sys as _sys

        (pytester.path / "test_fail.yaml").write_text(
            "name: yaml-fail\n"
            "fixtures:\n  - fixtures/f.yaml\n"
            "agent:\n"
            f"  command: {_sys.executable} agent.py\n"
            "  timeout_s: 10\n"
            "cases:\n"
            "  - name: expects wrong tool\n"
            "    input: \"\"\n"
            "    assertions:\n"
            "      - tool_called: nonexistent\n"
        )
        result = pytester.runpytest("-q")
        result.assert_outcomes(failed=1)

    def test_broken_yaml_collection_error(self, pytester: pytest.Pytester) -> None:
        (pytester.path / "test_broken.yaml").write_text("[unclosed\n")
        result = pytester.runpytest("-q")
        # Broken YAML manifests as a collection error, not a test failure.
        assert result.ret != 0

    def test_non_test_yaml_ignored(self, pytester: pytest.Pytester) -> None:
        # A YAML file that does not match test_*.{yaml,yml} nor *_test.{yaml,yml}
        # must be invisible to the collection hook.
        (pytester.path / "fixture_data.yaml").write_text("x: 1\n")
        pytester.makepyfile(
            "def test_dummy(): assert True"
        )
        result = pytester.runpytest("-q")
        result.assert_outcomes(passed=1)

    def test_yml_extension_also_collected(self, pytester: pytest.Pytester) -> None:
        (pytester.path / "fixtures").mkdir()
        (pytester.path / "fixtures" / "f.yaml").write_text(
            "server: { name: nested }\n"
            "tools:\n"
            "  - name: greet\n"
            "    responses:\n"
            "      - return: { ok: true }\n"
        )
        (pytester.path / "agent.py").write_text(_REAL_AGENT)

        import sys as _sys

        (pytester.path / "my_test.yml").write_text(
            "name: yml-ext\n"
            "fixtures:\n  - fixtures/f.yaml\n"
            "agent:\n"
            f"  command: {_sys.executable} agent.py\n"
            "  timeout_s: 10\n"
            "cases:\n"
            "  - name: greet\n"
            "    input: x\n"
            "    assertions:\n"
            "      - tool_called: greet\n"
        )
        result = pytester.runpytest("-q")
        result.assert_outcomes(passed=1)

    def test_agent_that_fails_to_complete(self, pytester: pytest.Pytester) -> None:
        (pytester.path / "fixtures").mkdir()
        (pytester.path / "fixtures" / "f.yaml").write_text(
            "server: { name: nested }\n"
            "tools:\n"
            "  - name: greet\n"
            "    responses:\n"
            "      - return: { ok: true }\n"
        )
        (pytester.path / "test_crash.yaml").write_text(
            "name: crash\n"
            "fixtures:\n  - fixtures/f.yaml\n"
            "agent:\n"
            "  command: /bin/false\n"
            "  timeout_s: 10\n"
            "cases:\n"
            "  - name: no assertions but agent fails\n"
            "    input: \"\"\n"
        )
        result = pytester.runpytest("-q")
        result.assert_outcomes(failed=1)


# ---------------------------------------------------------------------------
# Top-level `mcptest.mock` lazy import
# ---------------------------------------------------------------------------


class TestTopLevelReexport:
    def test_mock_accessible(self) -> None:
        import mcptest

        assert callable(mcptest.mock)

    def test_unknown_attribute_raises(self) -> None:
        import mcptest

        with pytest.raises(AttributeError):
            _ = mcptest.no_such_thing
