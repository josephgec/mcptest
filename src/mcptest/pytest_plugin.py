"""pytest plugin — decorator, fixtures, and YAML test-file collection.

The plugin provides three things:

1. `@mcptest.mock(*fixtures, agent=...)` — decorator that attaches runner
   configuration to a test function. Combined with the `mcptest_runner`
   fixture, tests can run their agent with one call:

   ```python
   from mcptest import mock

   @mock("fixtures/github.yaml", agent="python my_agent.py")
   def test_creates_issue(mcptest_runner):
       trace = mcptest_runner.run("File a bug")
       assert trace.call_count("create_issue") == 1
   ```

2. `mcptest_runner` and `mcptest_trace` fixtures — the first returns a
   ready-to-use `Runner` bound to the configured fixtures + agent; the
   second is a scratch `Trace` for tests that want to build one manually.

3. Collection of YAML test files — pytest picks up `test_*.yaml` and
   `*_test.yaml` files under the test root and exposes each TestCase
   as a pytest node. Each case runs its Runner and evaluates its
   assertions, reporting pass/fail through pytest's normal machinery.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import pytest

if TYPE_CHECKING:
    from mcptest.runner import AgentAdapter, Runner, Trace
    from mcptest.testspec.models import TestCase, TestSuite

# NOTE: all mcptest imports below are deliberately deferred inside functions.
# This module is loaded by pytest's `pytest11` entry point BEFORE pytest-cov
# starts instrumenting. Importing the rest of mcptest here would pull its
# submodules into the Python module cache before coverage sees them, dropping
# reported coverage to ~60%. Keeping the imports lazy means coverage measures
# them the first time a test triggers them, which is how every other mcptest
# subsystem is measured.


# ---------------------------------------------------------------------------
# `@mock` decorator
# ---------------------------------------------------------------------------


_CONFIG_ATTR = "_mcptest_config"


@dataclass
class _MockConfig:
    fixtures: tuple[str, ...]
    agent: Any
    cwd: Path | None


def mock(
    *fixtures: str,
    agent: Any = None,
    cwd: str | Path | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Attach mcptest runner config to a test function.

    `fixtures` — one or more paths to YAML fixture files. Relative paths
    resolve against the test file's directory at fixture time.

    `agent` — the agent to run. Accepts:

    - a string like `"python my_agent.py"`; parsed with `shlex.split` and
      handed to `SubprocessAdapter`. `python` is rewritten to
      `sys.executable` so tests run in the same venv as mcptest.
    - a Python callable with signature `(input, env) -> str | AgentResult`;
      wrapped in `CallableAdapter`.
    - a pre-built `AgentAdapter` instance; used as-is.

    `cwd` — optional working directory; defaults to the test file's
    parent directory.
    """
    config = _MockConfig(
        fixtures=tuple(fixtures),
        agent=agent,
        cwd=Path(cwd) if cwd is not None else None,
    )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, _CONFIG_ATTR, config)
        return func

    return decorator


def _resolve_agent(agent: Any, base_dir: Path) -> Any:
    from mcptest.runner.adapters import CallableAdapter, SubprocessAdapter
    from mcptest.testspec.models import AgentSpec

    if agent is None:
        raise pytest.UsageError(
            "mcptest_runner fixture requires an agent — pass agent=... to @mock"
        )
    if isinstance(agent, (SubprocessAdapter, CallableAdapter)):
        return agent
    if isinstance(agent, str):
        return AgentSpec(command=agent).build_adapter(base_dir)
    if callable(agent):
        return CallableAdapter(agent)
    return agent  # assume pre-built adapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mcptest_runner(request: pytest.FixtureRequest) -> Any:
    """Build a `Runner` from the surrounding test's `@mock(...)` config."""
    from mcptest.runner import Runner

    config: _MockConfig | None = getattr(request.function, _CONFIG_ATTR, None)
    if config is None:
        raise pytest.UsageError(
            "mcptest_runner fixture requires @mcptest.mock(...) on the test function"
        )

    test_file = Path(request.fspath)
    base_dir = config.cwd or test_file.parent

    fixture_paths: list[str] = []
    for f in config.fixtures:
        p = Path(f)
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        fixture_paths.append(str(p))

    adapter = _resolve_agent(config.agent, base_dir)
    return Runner(fixtures=fixture_paths, agent=adapter)


@pytest.fixture
def mcptest_trace() -> Any:
    """Return a fresh empty Trace — useful for building one by hand."""
    from mcptest.runner import Trace

    return Trace()


# ---------------------------------------------------------------------------
# YAML test-file collection
# ---------------------------------------------------------------------------


class McptestYamlFile(pytest.File):
    """A pytest File node representing one YAML test file."""

    def collect(self):
        from mcptest.testspec.loader import TestSuiteLoadError, load_test_suite

        try:
            suite = load_test_suite(self.path)
        except TestSuiteLoadError as exc:
            raise pytest.Collector.CollectError(str(exc)) from exc
        for case in suite.cases:
            yield McptestYamlCase.from_parent(
                self, name=f"{suite.name}::{case.name}", suite=suite, case=case
            )


class McptestYamlCase(pytest.Item):
    """A pytest Item representing one case from a YAML test file."""

    def __init__(
        self,
        *,
        suite: Any,
        case: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.suite = suite
        self.case = case

    def runtest(self) -> None:
        from mcptest.assertions import (
            AssertionResult,
            McpTestAssertionError,
            check_all,
            parse_assertions,
        )
        from mcptest.runner import Runner

        base_dir = self.path.parent
        fixture_paths = self.suite.resolve_fixtures(base_dir)
        adapter = self.suite.agent.build_adapter(base_dir)
        runner = Runner(fixtures=fixture_paths, agent=adapter)

        trace = runner.run(self.case.input)
        assertions = parse_assertions(self.case.assertions)
        results = check_all(assertions, trace)

        failures = [r for r in results if not r.passed]
        if failures or not trace.succeeded:
            if failures:
                result = failures[0]
            else:
                result = AssertionResult(
                    passed=False,
                    name="agent_run",
                    message=(
                        f"agent did not complete (exit={trace.exit_code}, "
                        f"error={trace.agent_error})"
                    ),
                )
            raise McpTestAssertionError(result)

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException]) -> str:
        from mcptest.assertions import McpTestAssertionError

        if isinstance(excinfo.value, McpTestAssertionError):
            return excinfo.value.result.message
        return super().repr_failure(excinfo)

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, f"{self.suite.name}::{self.case.name}"


def pytest_collect_file(parent: pytest.Collector, file_path: Path) -> Any:
    if file_path.suffix not in (".yaml", ".yml"):
        return None
    name = file_path.name
    if not (name.startswith("test_") or name.endswith(("_test.yaml", "_test.yml"))):
        return None
    return McptestYamlFile.from_parent(parent, path=file_path)
