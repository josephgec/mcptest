"""The test runner ties fixtures + adapters + traces together.

Typical usage:

```python
from mcptest.runner import Runner, PythonScriptAdapter

runner = Runner(
    fixtures=["fixtures/github.yaml"],
    agent=PythonScriptAdapter("examples/issue_agent.py"),
)
trace = runner.run("File a bug: login 500 error on Safari")
assert trace.call_count("create_issue") == 1
```

The runner's responsibility is narrow:

1. Load and validate every fixture listed.
2. Create a fresh JSONL trace file and export it to the agent via the
   `MCPTEST_TRACE_FILE` env var.
3. Also export a JSON manifest of fixture paths via `MCPTEST_FIXTURES` so the
   agent (or our `mcptest`-provided helper) knows which mock servers to
   spawn.
4. Invoke the adapter with the input string.
5. Read the trace file back, merge tool calls by timestamp, and return a
   `Trace` object.

The runner does *not* spawn mock server subprocesses itself — that is the
agent's job under MCP's standard client-spawns-server model. A scripted
agent shipped with mcptest (Session 6) makes this trivial in tests.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcptest.fixtures.loader import load_fixtures
from mcptest.fixtures.models import Fixture
from mcptest.mock_server.recorder import TRACE_FILE_ENV, read_trace_file
from mcptest.runner.adapters import AgentAdapter, AgentResult
from mcptest.runner.trace import Trace


FIXTURES_ENV = "MCPTEST_FIXTURES"


class RunnerError(Exception):
    """Raised when the runner cannot complete a run (setup-level failure)."""


@dataclass
class Runner:
    """Orchestrates one or more agent runs against a fixed set of fixtures."""

    fixtures: list[str | Path]
    agent: AgentAdapter
    workdir: Path | None = None
    keep_traces: bool = False
    extra_env: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.fixtures:
            raise RunnerError("Runner requires at least one fixture")
        # Eager-load so callers see fixture errors immediately at construction.
        self._loaded: list[Fixture] = load_fixtures(self.fixtures)
        self._fixture_paths: list[str] = [
            str(Path(p).resolve()) for p in self.fixtures
        ]

    @property
    def loaded_fixtures(self) -> list[Fixture]:
        return list(self._loaded)

    def run(self, input: str = "", *, metadata: dict[str, Any] | None = None) -> Trace:
        """Run the agent once with the given input and return the resulting trace."""
        work_root = self.workdir or Path(tempfile.gettempdir()) / "mcptest"
        work_root.mkdir(parents=True, exist_ok=True)

        run_id = uuid.uuid4().hex[:12]
        trace_file = work_root / f"trace-{run_id}.jsonl"
        trace_file.touch()

        env: dict[str, str] = {
            TRACE_FILE_ENV: str(trace_file),
            FIXTURES_ENV: json.dumps(self._fixture_paths),
            "MCPTEST_RUN_ID": run_id,
        }
        if self.extra_env:
            env.update(self.extra_env)

        started = time.monotonic()
        try:
            result = self.agent.run(input, env)
        finally:
            duration_s = time.monotonic() - started

        tool_calls = read_trace_file(trace_file)
        trace = Trace(
            input=input,
            output=result.output,
            tool_calls=tool_calls,
            duration_s=max(duration_s, result.duration_s),
            exit_code=result.exit_code,
            stderr=result.stderr,
            agent_error=result.error,
            metadata=metadata or {},
        )
        trace.metadata.setdefault("run_id", run_id)
        trace.metadata.setdefault(
            "fixtures", [f.server.name for f in self._loaded]
        )

        if not self.keep_traces:
            try:
                os.unlink(trace_file)
            except FileNotFoundError:  # pragma: no cover
                pass

        return trace

    def run_many(self, inputs: list[str]) -> list[Trace]:
        """Run the agent once per input, returning a trace per input."""
        return [self.run(i) for i in inputs]

    def __enter__(self) -> Runner:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None
