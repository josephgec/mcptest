"""Tests for mcptest.runner.parallel — parallel case execution engine.

Session 22: parallel test execution (-j N).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from mcptest.runner import CallableAdapter, Runner
from mcptest.runner.adapters import AgentResult
from mcptest.runner.parallel import CaseWork, ParallelConfig, run_cases_parallel
from mcptest.testspec.models import AgentSpec
from mcptest.testspec.models import TestCase as CaseSpec
from mcptest.testspec.models import TestSuite as SuiteSpec


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fixture_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "fix.yaml"
    p.write_text(
        "server: {name: mock}\n"
        "tools:\n"
        "  - name: ping\n"
        "    responses:\n"
        "      - return_text: pong\n"
    )
    return p


def _passing_runner(tmp_path: Path, delay: float = 0.0) -> Runner:
    """Build a runner whose agent always exits 0."""
    fix = _fixture_yaml(tmp_path)

    def agent_fn(inp: str, env: dict[str, str]) -> str:
        if delay > 0:
            time.sleep(delay)
        return "ok"

    return Runner(fixtures=[str(fix)], agent=CallableAdapter(agent_fn))


def _failing_runner(tmp_path: Path) -> Runner:
    """Build a runner whose agent exits non-zero (trace.succeeded=False)."""
    fix = _fixture_yaml(tmp_path)

    def agent_fn(inp: str, env: dict[str, str]) -> AgentResult:
        return AgentResult(output="fail", exit_code=1)

    return Runner(fixtures=[str(fix)], agent=CallableAdapter(agent_fn))


def _make_suite(
    name: str = "s",
    cases: list[CaseSpec] | None = None,
    parallel: bool = True,
) -> SuiteSpec:
    return SuiteSpec(
        name=name,
        agent=AgentSpec(command="echo noop"),  # not used; runner is passed in CaseWork
        cases=cases or [CaseSpec(name="c")],
        parallel=parallel,
    )


def _make_case(name: str = "c", assertions: list[dict[str, Any]] | None = None) -> CaseSpec:
    return CaseSpec(name=name, assertions=assertions or [])


def _make_work(
    runner: Runner,
    suite: SuiteSpec | None = None,
    case: CaseSpec | None = None,
) -> CaseWork:
    s = suite or _make_suite()
    c = case or _make_case()
    return CaseWork(suite=s, case=c, runner=runner)


# ---------------------------------------------------------------------------
# 1. ParallelConfig — dataclass contract
# ---------------------------------------------------------------------------


class TestParallelConfig:
    def test_stores_max_workers(self) -> None:
        cfg = ParallelConfig(max_workers=4)
        assert cfg.max_workers == 4

    def test_fail_fast_defaults_false(self) -> None:
        cfg = ParallelConfig(max_workers=2)
        assert cfg.fail_fast is False

    def test_fail_fast_explicit(self) -> None:
        cfg = ParallelConfig(max_workers=2, fail_fast=True)
        assert cfg.fail_fast is True

    def test_frozen(self) -> None:
        cfg = ParallelConfig(max_workers=2)
        with pytest.raises((AttributeError, TypeError)):
            cfg.max_workers = 99  # type: ignore[misc]

    def test_zero_workers_allowed(self) -> None:
        cfg = ParallelConfig(max_workers=0)
        assert cfg.max_workers == 0


# ---------------------------------------------------------------------------
# 2. CaseWork — dataclass contract
# ---------------------------------------------------------------------------


class TestCaseWork:
    def test_stores_components(self, tmp_path: Path) -> None:
        runner = _passing_runner(tmp_path)
        suite = _make_suite()
        case = _make_case()
        w = CaseWork(suite=suite, case=case, runner=runner)
        assert w.suite is suite
        assert w.case is case
        assert w.runner is runner

    def test_mutable(self, tmp_path: Path) -> None:
        """CaseWork is not frozen; attributes can be replaced if needed."""
        runner = _passing_runner(tmp_path)
        w = _make_work(runner)
        new_case = _make_case("new")
        w.case = new_case
        assert w.case.name == "new"


# ---------------------------------------------------------------------------
# 3. run_cases_parallel — empty / edge cases
# ---------------------------------------------------------------------------


class TestRunCasesParallelEmpty:
    def test_empty_list_returns_empty(self) -> None:
        cfg = ParallelConfig(max_workers=4)
        result = run_cases_parallel([], cfg)
        assert result == []

    def test_single_item(self, tmp_path: Path) -> None:
        runner = _passing_runner(tmp_path)
        work = [_make_work(runner)]
        cfg = ParallelConfig(max_workers=1)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 1
        assert results[0].passed is True

    def test_workers_larger_than_cases(self, tmp_path: Path) -> None:
        """Requesting more workers than cases should not raise."""
        runner = _passing_runner(tmp_path)
        work = [_make_work(runner, case=_make_case(str(i))) for i in range(3)]
        cfg = ParallelConfig(max_workers=100)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# 4. run_cases_parallel — result correctness
# ---------------------------------------------------------------------------


class TestRunCasesParallelResults:
    def test_all_passing_cases_collected(self, tmp_path: Path) -> None:
        runner = _passing_runner(tmp_path)
        cases = [_make_case(str(i)) for i in range(4)]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]
        cfg = ParallelConfig(max_workers=2)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 4
        assert all(r.passed for r in results)

    def test_results_in_submission_order(self, tmp_path: Path) -> None:
        """Results must match submission order regardless of completion order."""
        runner = _passing_runner(tmp_path)
        case_names = [f"case-{i}" for i in range(6)]
        cases = [_make_case(n) for n in case_names]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]
        cfg = ParallelConfig(max_workers=3)
        results = run_cases_parallel(work, cfg)
        assert [r.case_name for r in results] == case_names

    def test_suite_name_propagated(self, tmp_path: Path) -> None:
        runner = _passing_runner(tmp_path)
        suite = _make_suite(name="my-suite")
        work = [_make_work(runner, suite=suite)]
        cfg = ParallelConfig(max_workers=1)
        results = run_cases_parallel(work, cfg)
        assert results[0].suite_name == "my-suite"

    def test_case_name_propagated(self, tmp_path: Path) -> None:
        runner = _passing_runner(tmp_path)
        work = [_make_work(runner, case=_make_case("my-case"))]
        cfg = ParallelConfig(max_workers=1)
        results = run_cases_parallel(work, cfg)
        assert results[0].case_name == "my-case"

    def test_failing_case_reported(self, tmp_path: Path) -> None:
        """A case with a failing assertion is passed=False in results."""
        runner = _passing_runner(tmp_path)
        # tool_called: nonexistent will fail since runner writes no tool calls.
        case = _make_case(assertions=[{"tool_called": "nonexistent"}])
        work = [_make_work(runner, case=case)]
        cfg = ParallelConfig(max_workers=1)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 1
        assert results[0].passed is False

    def test_mixed_pass_fail(self, tmp_path: Path) -> None:
        runner = _passing_runner(tmp_path)
        passing_case = _make_case("pass")
        failing_case = _make_case("fail", assertions=[{"tool_called": "nope"}])
        suite = _make_suite(cases=[passing_case, failing_case])
        work = [
            CaseWork(suite=suite, case=passing_case, runner=runner),
            CaseWork(suite=suite, case=failing_case, runner=runner),
        ]
        cfg = ParallelConfig(max_workers=2)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False


# ---------------------------------------------------------------------------
# 5. fail_fast behaviour
# ---------------------------------------------------------------------------


class TestRunCasesParallelFailFast:
    def test_fail_fast_stops_after_first_failure(self, tmp_path: Path) -> None:
        """With max_workers=1 and fail_fast, far fewer cases run than submitted.

        The exact count is non-deterministic because the single worker thread
        may start the next task before the cancel() call in the main thread
        reaches the queue (the "cancel race").  We verify the key invariant:
        not all cases run, and the failing case is always present.
        """
        runner = _passing_runner(tmp_path)
        failing_case = _make_case("f0", assertions=[{"tool_called": "nope"}])
        other_cases = [_make_case(f"ok{i}") for i in range(5)]
        all_cases = [failing_case] + other_cases
        suite = _make_suite(cases=all_cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in all_cases]
        cfg = ParallelConfig(max_workers=1, fail_fast=True)
        results = run_cases_parallel(work, cfg)
        # With max_workers=1 at most 2 results come back (failing + 1 race winner).
        assert 1 <= len(results) <= 2
        assert results[0].passed is False
        # Critically: not all 6 cases ran.
        assert len(results) < len(work)

    def test_fail_fast_false_runs_all(self, tmp_path: Path) -> None:
        """Without fail_fast, all cases run even when some fail."""
        runner = _passing_runner(tmp_path)
        cases = [
            _make_case("f", assertions=[{"tool_called": "nope"}]),
            _make_case("ok"),
        ]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]
        cfg = ParallelConfig(max_workers=1, fail_fast=False)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 2

    def test_fail_fast_all_passing_returns_all(self, tmp_path: Path) -> None:
        """fail_fast has no effect when every case passes."""
        runner = _passing_runner(tmp_path)
        cases = [_make_case(str(i)) for i in range(4)]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]
        cfg = ParallelConfig(max_workers=1, fail_fast=True)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 4
        assert all(r.passed for r in results)


# ---------------------------------------------------------------------------
# 6. Worker count — auto-detect (j=0)
# ---------------------------------------------------------------------------


class TestRunCasesParallelWorkerCount:
    def test_auto_detect_uses_cpu_count(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """max_workers=0 resolves to os.cpu_count() (mocked here to 8)."""
        monkeypatch.setattr(os, "cpu_count", lambda: 8)
        runner = _passing_runner(tmp_path)
        cases = [_make_case(str(i)) for i in range(4)]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]
        cfg = ParallelConfig(max_workers=0)
        # Just verify it completes without error.
        results = run_cases_parallel(work, cfg)
        assert len(results) == 4

    def test_auto_detect_capped_at_case_count(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When cpu_count > case count, workers are capped to case count."""
        monkeypatch.setattr(os, "cpu_count", lambda: 100)
        runner = _passing_runner(tmp_path)
        work = [_make_work(runner, case=_make_case(str(i))) for i in range(3)]
        cfg = ParallelConfig(max_workers=0)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 3

    def test_explicit_workers_capped_at_case_count(self, tmp_path: Path) -> None:
        """Explicit workers=50 with 2 cases uses at most 2 threads."""
        runner = _passing_runner(tmp_path)
        work = [_make_work(runner, case=_make_case(str(i))) for i in range(2)]
        cfg = ParallelConfig(max_workers=50)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 2

    def test_cpu_count_none_falls_back_to_one(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If os.cpu_count() returns None, fall back to 1."""
        monkeypatch.setattr(os, "cpu_count", lambda: None)
        runner = _passing_runner(tmp_path)
        work = [_make_work(runner, case=_make_case(str(i))) for i in range(3)]
        cfg = ParallelConfig(max_workers=0)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# 7. on_result progress callback
# ---------------------------------------------------------------------------


class TestRunCasesParallelCallback:
    def test_callback_called_once_per_case(self, tmp_path: Path) -> None:
        runner = _passing_runner(tmp_path)
        cases = [_make_case(str(i)) for i in range(5)]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]
        cfg = ParallelConfig(max_workers=2)
        fired: list[Any] = []
        run_cases_parallel(work, cfg, on_result=fired.append)
        assert len(fired) == 5

    def test_callback_receives_case_result(self, tmp_path: Path) -> None:
        from mcptest.cli.commands import CaseResult

        runner = _passing_runner(tmp_path)
        work = [_make_work(runner, case=_make_case("c1"))]
        cfg = ParallelConfig(max_workers=1)
        received: list[Any] = []
        run_cases_parallel(work, cfg, on_result=received.append)
        assert len(received) == 1
        assert isinstance(received[0], CaseResult)
        assert received[0].case_name == "c1"

    def test_callback_not_called_for_cancelled(self, tmp_path: Path) -> None:
        """Cancelled futures (fail_fast) do not trigger on_result.

        The cancel race means at most 2 callbacks fire with max_workers=1.
        """
        runner = _passing_runner(tmp_path)
        failing = _make_case("f", assertions=[{"tool_called": "nope"}])
        others = [_make_case(f"o{i}") for i in range(5)]
        all_cases = [failing] + others
        suite = _make_suite(cases=all_cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in all_cases]
        cfg = ParallelConfig(max_workers=1, fail_fast=True)
        fired: list[Any] = []
        run_cases_parallel(work, cfg, on_result=fired.append)
        # At most 2 callbacks fire (1 failing + 1 cancel-race winner).
        assert 1 <= len(fired) <= 2
        assert len(fired) < len(work)

    def test_callback_none_does_not_crash(self, tmp_path: Path) -> None:
        runner = _passing_runner(tmp_path)
        work = [_make_work(runner)]
        cfg = ParallelConfig(max_workers=1)
        results = run_cases_parallel(work, cfg, on_result=None)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# 8. retry_override and tolerance_override pass-through
# ---------------------------------------------------------------------------


class TestRunCasesParallelOverrides:
    def test_retry_override_multiplies_agent_calls(self, tmp_path: Path) -> None:
        """retry_override=3 runs each case's agent 3 times."""
        fix = _fixture_yaml(tmp_path)
        call_count = {"n": 0}
        lock = threading.Lock()

        def counting_agent(inp: str, env: dict[str, str]) -> str:
            with lock:
                call_count["n"] += 1
            return "ok"

        runner = Runner(fixtures=[str(fix)], agent=CallableAdapter(counting_agent))
        n_cases = 4
        cases = [_make_case(str(i)) for i in range(n_cases)]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]
        cfg = ParallelConfig(max_workers=n_cases)
        run_cases_parallel(work, cfg, retry_override=3)
        assert call_count["n"] == n_cases * 3

    def test_tolerance_override_applied(self, tmp_path: Path) -> None:
        """tolerance_override=0.5 allows 50% of attempts to fail."""
        fix = _fixture_yaml(tmp_path)
        attempt_idx = {"n": 0}
        lock = threading.Lock()

        def alternating_agent(inp: str, env: dict[str, str]) -> AgentResult:
            with lock:
                idx = attempt_idx["n"]
                attempt_idx["n"] += 1
            # Even attempts succeed; odd attempts fail.
            return AgentResult(output="x", exit_code=0 if idx % 2 == 0 else 1)

        runner = Runner(fixtures=[str(fix)], agent=CallableAdapter(alternating_agent))
        case = _make_case("c")
        suite = _make_suite(cases=[case])
        work = [CaseWork(suite=suite, case=case, runner=runner)]
        cfg = ParallelConfig(max_workers=1)
        results = run_cases_parallel(
            work, cfg, retry_override=2, tolerance_override=0.5
        )
        assert len(results) == 1
        # 1 pass out of 2 attempts = 0.5 pass rate >= 0.5 tolerance → passed.
        assert results[0].passed is True

    def test_no_override_uses_case_defaults(self, tmp_path: Path) -> None:
        """When overrides are None, per-case retry/tolerance values are used."""
        runner = _passing_runner(tmp_path)
        case = CaseSpec(name="c", retry=1, tolerance=1.0)
        suite = _make_suite(cases=[case])
        work = [CaseWork(suite=suite, case=case, runner=runner)]
        cfg = ParallelConfig(max_workers=1)
        results = run_cases_parallel(work, cfg)
        assert len(results) == 1
        assert results[0].passed is True


# ---------------------------------------------------------------------------
# 9. Thread safety — distinct trace IDs per concurrent run
# ---------------------------------------------------------------------------


class TestRunCasesParallelThreadSafety:
    def test_concurrent_runs_produce_distinct_trace_ids(self, tmp_path: Path) -> None:
        """Each worker produces a trace with a unique run_id."""
        runner = _passing_runner(tmp_path)
        n = 8
        cases = [_make_case(str(i)) for i in range(n)]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]
        cfg = ParallelConfig(max_workers=n)
        results = run_cases_parallel(work, cfg)
        run_ids = [r.trace.metadata.get("run_id") for r in results]
        assert len(set(run_ids)) == n, f"Expected {n} unique run_ids, got {set(run_ids)}"

    def test_shared_runner_is_thread_safe(self, tmp_path: Path) -> None:
        """A single Runner instance shared across many cases must not corrupt traces."""
        runner = _passing_runner(tmp_path)
        n = 12
        cases = [_make_case(str(i)) for i in range(n)]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]
        cfg = ParallelConfig(max_workers=4)
        results = run_cases_parallel(work, cfg)
        assert len(results) == n
        # All case names should survive without mixing.
        assert {r.case_name for r in results} == {str(i) for i in range(n)}


# ---------------------------------------------------------------------------
# 10. Timing — parallel is faster than serial
# ---------------------------------------------------------------------------


class TestRunCasesParallelTiming:
    def test_parallel_faster_than_serial(self, tmp_path: Path) -> None:
        """4 cases each sleeping 50 ms run in ~50 ms with j=4, not ~200 ms."""
        delay = 0.05
        n = 4
        runner = _passing_runner(tmp_path, delay=delay)
        cases = [_make_case(str(i)) for i in range(n)]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]

        cfg_serial = ParallelConfig(max_workers=1)
        t0 = time.perf_counter()
        run_cases_parallel(work, cfg_serial)
        serial_wall = time.perf_counter() - t0

        cfg_parallel = ParallelConfig(max_workers=n)
        t0 = time.perf_counter()
        run_cases_parallel(work, cfg_parallel)
        parallel_wall = time.perf_counter() - t0

        # Parallel should be at least 2× faster than serial.
        assert parallel_wall < serial_wall / 2, (
            f"parallel={parallel_wall:.3f}s is not significantly faster "
            f"than serial={serial_wall:.3f}s"
        )


# ---------------------------------------------------------------------------
# 11. j=1 parity with serial path
# ---------------------------------------------------------------------------


class TestJOneParityWithSerial:
    def test_j1_produces_same_results_as_serial(self, tmp_path: Path) -> None:
        """j=1 through run_cases_parallel should give identical results to
        running _run_case sequentially."""
        from mcptest.cli.commands import _run_case

        runner = _passing_runner(tmp_path)
        cases = [_make_case(str(i)) for i in range(4)]
        suite = _make_suite(cases=cases)
        work = [CaseWork(suite=suite, case=c, runner=runner) for c in cases]

        cfg = ParallelConfig(max_workers=1)
        parallel_results = run_cases_parallel(work, cfg)

        serial_results = [_run_case(runner, suite, c) for c in cases]

        assert len(parallel_results) == len(serial_results)
        for p, s in zip(parallel_results, serial_results):
            assert p.passed == s.passed
            assert p.case_name == s.case_name
            assert p.suite_name == s.suite_name


# ---------------------------------------------------------------------------
# 12. TestSuite.parallel field
# ---------------------------------------------------------------------------


class TestSuiteParallelField:
    def test_parallel_defaults_true(self) -> None:
        suite = _make_suite()
        assert suite.parallel is True

    def test_parallel_false(self) -> None:
        suite = _make_suite(parallel=False)
        assert suite.parallel is False

    def test_parallel_field_parsed_from_yaml(self, tmp_path: Path) -> None:
        import yaml
        from mcptest.testspec.loader import load_test_suite

        test_file = tmp_path / "t.yaml"
        test_file.write_text(
            "name: s\n"
            "fixtures: []\n"
            "parallel: false\n"
            "agent:\n"
            "  command: echo noop\n"
            "cases:\n"
            "  - name: c\n"
        )
        suite = load_test_suite(test_file)
        assert suite.parallel is False

    def test_parallel_true_explicit_in_yaml(self, tmp_path: Path) -> None:
        from mcptest.testspec.loader import load_test_suite

        test_file = tmp_path / "t.yaml"
        test_file.write_text(
            "name: s\n"
            "fixtures: []\n"
            "parallel: true\n"
            "agent:\n"
            "  command: echo noop\n"
            "cases:\n"
            "  - name: c\n"
        )
        suite = load_test_suite(test_file)
        assert suite.parallel is True
