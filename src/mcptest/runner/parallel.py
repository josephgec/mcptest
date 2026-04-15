"""Parallel test-case execution engine using ThreadPoolExecutor."""

from __future__ import annotations

import os
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ParallelConfig:
    """Configuration for parallel case execution.

    Attributes:
        max_workers: Number of worker threads.  0 = auto-detect via
            ``os.cpu_count()``, capped at the number of work items.
        fail_fast: When True, cancel remaining futures on the first failure.
    """

    max_workers: int
    fail_fast: bool = False


@dataclass
class CaseWork:
    """Unit of work: one test case to execute within a suite context.

    The runner is shared among all cases in the same suite, but
    ``runner.run()`` is thread-safe: each call creates its own UUID-scoped
    trace file and subprocess, with no shared mutable state.
    """

    suite: Any  # TestSuite — type-erased to break the circular import chain
    case: Any  # TestCase
    runner: Any  # Runner


def run_cases_parallel(
    work_items: list[CaseWork],
    config: ParallelConfig,
    *,
    retry_override: int | None = None,
    tolerance_override: float | None = None,
    on_result: Callable[[Any], None] | None = None,
) -> list[Any]:  # list[CaseResult]
    """Run test cases in parallel, returning results in submission order.

    Uses a :class:`ThreadPoolExecutor`: each worker calls ``runner.run()``
    which spawns a subprocess.  Python releases the GIL during
    ``subprocess.run()``, so threads parallelize effectively without a
    process pool.

    Results are returned in submission order (identical to serial
    execution).  Cases that were cancelled because of *fail_fast* are
    omitted from the returned list.

    Args:
        work_items: Cases to run.  Each carries its suite, case, and runner.
        config: Controls worker count and fail-fast behaviour.
        retry_override: Override retry count on every case (optional).
        tolerance_override: Override pass-rate tolerance on every case
            (optional).
        on_result: Called once per completed result, useful for real-time
            progress display.

    Returns:
        ``CaseResult`` list in submission order, minus any cancelled cases.
    """
    if not work_items:
        return []

    # Resolve worker count: 0 = auto-detect, always capped to work count.
    workers = config.max_workers
    if workers == 0:
        workers = min(os.cpu_count() or 1, len(work_items))
    else:
        workers = min(workers, len(work_items))

    # Lazy import to break the commands ↔ parallel circular import.
    from mcptest.cli.commands import _run_case  # noqa: PLC0415

    # Pre-allocate slots so we can restore submission order at the end.
    results: list[Any | None] = [None] * len(work_items)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx: dict[Future[Any], int] = {}
        for idx, work in enumerate(work_items):
            fut = executor.submit(
                _run_case,
                work.runner,
                work.suite,
                work.case,
                retry_override=retry_override,
                tolerance_override=tolerance_override,
            )
            future_to_idx[fut] = idx

        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                result = fut.result()
            except CancelledError:
                # Future was cancelled by fail_fast; leave the slot empty.
                continue
            results[idx] = result
            if on_result is not None:
                on_result(result)
            if config.fail_fast and not result.passed:
                # Cancel futures that have not been picked up by a thread yet.
                for f in list(future_to_idx):
                    if not f.done():
                        f.cancel()

    # Filter out empty slots from cancelled cases while preserving order.
    return [r for r in results if r is not None]
