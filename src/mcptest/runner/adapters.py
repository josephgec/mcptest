"""Agent adapters — plug different agent invocation styles into the runner.

An `AgentAdapter` takes a user input string + environment dict and produces an
`AgentResult` containing the agent's final textual output and process state.
Three adapters ship out of the box:

- `SubprocessAdapter`: spawn the agent as a shell command. Input is fed via
  stdin; output is read from stdout. Environment variables are layered on top
  of `os.environ` so `MCPTEST_TRACE_FILE` et al. propagate into the child.
- `CallableAdapter`: call a Python function directly. Useful for in-process
  scripted agents and for unit-testing the runner itself.
- `PythonScriptAdapter`: sugar over `SubprocessAdapter` for running a Python
  script via the current interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class AgentResult:
    """What the runner needs to know about how an agent ran."""

    output: str
    stderr: str = ""
    exit_code: int = 0
    duration_s: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentAdapter(Protocol):
    """An adapter that knows how to run one agent invocation."""

    def run(self, input: str, env: dict[str, str]) -> AgentResult: ...


@dataclass
class SubprocessAdapter:
    """Run an agent as a subprocess over stdin/stdout.

    Attributes:
        command: The first argument (executable name or path).
        args: Additional arguments.
        env: Extra environment variables merged over `os.environ`.
        cwd: Working directory for the child process.
        timeout_s: Kill and fail the run if it exceeds this many seconds.
        input_via: How to deliver the input string:
            - "stdin" (default): written to stdin then stdin is closed.
            - "arg": appended as the last argv.
            - "env:NAME": set as environment variable NAME.
    """

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout_s: float = 60.0
    input_via: str = "stdin"

    def run(self, input: str, env: dict[str, str]) -> AgentResult:
        full_env = {**os.environ, **self.env, **env}
        argv = [self.command, *self.args]
        stdin_payload: str | None = input

        if self.input_via == "stdin":
            stdin_payload = input
        elif self.input_via == "arg":
            argv.append(input)
            stdin_payload = None
        elif self.input_via.startswith("env:"):
            var_name = self.input_via.removeprefix("env:")
            full_env[var_name] = input
            stdin_payload = None
        else:  # pragma: no cover
            raise ValueError(
                f"unknown input_via {self.input_via!r}; expected 'stdin', 'arg', or 'env:NAME'"
            )

        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                input=stdin_payload,
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                env=full_env,
                cwd=self.cwd,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return AgentResult(
                output=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
                exit_code=-1,
                duration_s=time.monotonic() - started,
                error=f"agent timed out after {self.timeout_s}s",
            )
        except FileNotFoundError as exc:
            return AgentResult(
                output="",
                stderr="",
                exit_code=-1,
                duration_s=time.monotonic() - started,
                error=f"agent command not found: {exc}",
            )

        return AgentResult(
            output=completed.stdout or "",
            stderr=completed.stderr or "",
            exit_code=completed.returncode,
            duration_s=time.monotonic() - started,
            error=None,
        )


def PythonScriptAdapter(
    script: str,
    *,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float = 60.0,
    input_via: str = "stdin",
) -> SubprocessAdapter:
    """Convenience factory: run `sys.executable script.py ...`.

    Ensures we use the same interpreter (and venv) that's running the
    runner, which matters because the mock server is importable only
    from the mcptest install.
    """
    return SubprocessAdapter(
        command=sys.executable,
        args=[script, *(args or [])],
        env=env or {},
        timeout_s=timeout_s,
        input_via=input_via,
    )


@dataclass
class CallableAdapter:
    """Wrap a Python callable as an agent adapter.

    The callable receives `(input: str, env: dict[str, str])` and returns
    either a plain string (assumed to be the agent output) or an
    `AgentResult` for full control. Exceptions raised by the callable are
    captured into `AgentResult.error` rather than crashing the runner.
    """

    func: Callable[[str, dict[str, str]], Any]

    def run(self, input: str, env: dict[str, str]) -> AgentResult:
        started = time.monotonic()
        try:
            result = self.func(input, env)
        except Exception as exc:
            return AgentResult(
                output="",
                exit_code=-1,
                duration_s=time.monotonic() - started,
                error=f"{type(exc).__name__}: {exc}",
            )

        duration = time.monotonic() - started
        if isinstance(result, AgentResult):
            if result.duration_s == 0.0:
                result.duration_s = duration
            return result
        return AgentResult(output=str(result), duration_s=duration)
