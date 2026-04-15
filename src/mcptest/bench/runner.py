"""Benchmark execution engine.

The :class:`BenchmarkRunner` is the heart of ``mcptest bench``.  It iterates
over every :class:`~mcptest.bench.profile.AgentProfile`, creates an adapter
for each, and runs all discovered test cases against it.  Profiles run
**serially** (so timings are comparable); cases within one profile may be
parallelised in a future version.

The resulting :class:`BenchmarkEntry` list is consumed by
:class:`~mcptest.bench.report.BenchmarkReport` to build summaries and
rankings.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mcptest.bench.profile import AgentProfile
from mcptest.fixtures.loader import FixtureLoadError
from mcptest.metrics.base import MetricResult, compute_all
from mcptest.runner.adapters import AgentAdapter, SubprocessAdapter
from mcptest.runner.runner import Runner, RunnerError
from mcptest.runner.trace import Trace
from mcptest.testspec.loader import TestSuiteLoadError, discover_test_files, load_test_suite


@dataclass
class BenchmarkEntry:
    """Result of running one test case against one agent profile.

    Attributes:
        agent: Name of the :class:`~mcptest.bench.profile.AgentProfile`.
        suite: Name of the test suite (from the YAML ``name:`` field).
        case: Name of the test case.
        trace: The agent :class:`~mcptest.runner.trace.Trace` produced.
        metric_results: Metric scores computed against *trace*.
        passed: ``True`` iff the agent run succeeded (exit 0, no error).
        duration_s: Wall-clock time in seconds for this case.
        error: Non-``None`` when setup or execution failed before a trace
            could be collected.
    """

    agent: str
    suite: str
    case: str
    trace: Trace
    metric_results: list[MetricResult]
    passed: bool
    duration_s: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "suite": self.suite,
            "case": self.case,
            "passed": self.passed,
            "duration_s": self.duration_s,
            "error": self.error,
            "metrics": [m.to_dict() for m in self.metric_results],
            "trace": self.trace.to_dict(),
        }


@dataclass
class BenchmarkRunner:
    """Run a set of test cases against multiple agent profiles.

    Profiles are executed **serially** to keep timing comparisons fair.
    Cases within each profile run serially by default; the *parallel*
    parameter is reserved for future use.

    Parameters
    ----------
    profiles:
        Agent definitions to benchmark.
    test_path:
        Root directory (or single file) passed to
        :func:`~mcptest.testspec.loader.discover_test_files`.
    parallel:
        Reserved; currently ignored (cases always run serially).
    retry_override:
        If set, overrides each profile's ``retry`` value for all cases.
    tolerance_override:
        If set, overrides each profile's ``tolerance`` value for all cases.
    _adapter_factory:
        Optional callable ``(profile) -> AgentAdapter``.  When provided,
        it is used instead of building a :class:`SubprocessAdapter` from
        the profile's ``command``.  Intended for unit testing.
    """

    profiles: list[AgentProfile]
    test_path: str
    parallel: int = 1
    retry_override: int | None = None
    tolerance_override: float | None = None
    # For testing: inject a custom adapter factory instead of building
    # SubprocessAdapters from profile commands.
    _adapter_factory: Callable[[AgentProfile], AgentAdapter] | None = field(
        default=None, repr=False
    )

    def run(self) -> list[BenchmarkEntry]:
        """Execute the full benchmark matrix and return all entries.

        Profiles are run in order.  For each profile every discovered test
        file is loaded and every case is executed.  Errors at any stage
        (file load, runner setup, agent crash) produce an entry with
        ``passed=False`` and a descriptive ``error`` string rather than
        raising an exception — the caller always receives a complete result
        list.
        """
        files = discover_test_files(self.test_path)
        all_entries: list[BenchmarkEntry] = []

        for profile in self.profiles:
            adapter = self._make_adapter(profile)
            entries = self._run_profile(profile, adapter, files)
            all_entries.extend(entries)

        return all_entries

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _make_adapter(self, profile: AgentProfile) -> AgentAdapter:
        if self._adapter_factory is not None:
            return self._adapter_factory(profile)
        parts = profile.command.split()
        return SubprocessAdapter(
            command=parts[0],
            args=parts[1:] if len(parts) > 1 else [],
            env=profile.env,
        )

    def _run_profile(
        self,
        profile: AgentProfile,
        adapter: AgentAdapter,
        files: list[Path],
    ) -> list[BenchmarkEntry]:
        """Run all test cases in *files* using *adapter*."""
        entries: list[BenchmarkEntry] = []

        for test_file in files:
            try:
                suite = load_test_suite(test_file)
            except TestSuiteLoadError as exc:
                entries.append(
                    BenchmarkEntry(
                        agent=profile.name,
                        suite=str(test_file),
                        case="<load>",
                        trace=Trace(),
                        metric_results=[],
                        passed=False,
                        duration_s=0.0,
                        error=str(exc),
                    )
                )
                continue

            fixture_paths = suite.resolve_fixtures(test_file.parent)
            if not fixture_paths:
                entries.append(
                    BenchmarkEntry(
                        agent=profile.name,
                        suite=suite.name,
                        case="<setup>",
                        trace=Trace(),
                        metric_results=[],
                        passed=False,
                        duration_s=0.0,
                        error=(
                            "suite has no fixtures; "
                            "benchmarking requires at least one fixture"
                        ),
                    )
                )
                continue

            try:
                runner = Runner(fixtures=fixture_paths, agent=adapter)
            except (RunnerError, FixtureLoadError, ValueError) as exc:
                entries.append(
                    BenchmarkEntry(
                        agent=profile.name,
                        suite=suite.name,
                        case="<setup>",
                        trace=Trace(),
                        metric_results=[],
                        passed=False,
                        duration_s=0.0,
                        error=str(exc),
                    )
                )
                continue

            for case in suite.cases:
                retry = (
                    self.retry_override
                    if self.retry_override is not None
                    else profile.retry
                )
                tolerance = (
                    self.tolerance_override
                    if self.tolerance_override is not None
                    else profile.tolerance
                )

                t_start = time.monotonic()
                try:
                    if retry == 1:
                        trace = runner.run(case.input)
                        passed = trace.succeeded
                    else:
                        retry_result = runner.run_with_retry(
                            case.input,
                            retry=retry,
                            tolerance=tolerance,
                        )
                        trace = retry_result.traces[-1]
                        passed = retry_result.passed
                except Exception as exc:  # pragma: no cover — defensive
                    entries.append(
                        BenchmarkEntry(
                            agent=profile.name,
                            suite=suite.name,
                            case=case.name,
                            trace=Trace(input=case.input),
                            metric_results=[],
                            passed=False,
                            duration_s=time.monotonic() - t_start,
                            error=str(exc),
                        )
                    )
                    continue

                metric_results = compute_all(trace)
                entries.append(
                    BenchmarkEntry(
                        agent=profile.name,
                        suite=suite.name,
                        case=case.name,
                        trace=trace,
                        metric_results=metric_results,
                        passed=passed,
                        duration_s=trace.duration_s,
                    )
                )

        return entries
