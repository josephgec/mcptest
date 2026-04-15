"""Test runner — orchestrates mocks + agents + trace capture."""

from __future__ import annotations

from mcptest.runner.adapters import (
    AgentAdapter,
    AgentResult,
    CallableAdapter,
    PythonScriptAdapter,
    SubprocessAdapter,
)
from mcptest.runner.parallel import CaseWork, ParallelConfig, run_cases_parallel
from mcptest.runner.runner import Runner, RunnerError
from mcptest.runner.trace import RetryResult, Trace

__all__ = [
    "AgentAdapter",
    "AgentResult",
    "CallableAdapter",
    "CaseWork",
    "ParallelConfig",
    "PythonScriptAdapter",
    "RetryResult",
    "Runner",
    "RunnerError",
    "SubprocessAdapter",
    "Trace",
    "run_cases_parallel",
]
