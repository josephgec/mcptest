"""Concrete click subcommands for the mcptest CLI."""

from __future__ import annotations

import json as json_module
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

import click
from rich.console import Console
from rich.table import Table

from mcptest.assertions import (
    AssertionResult,
    check_all,
    parse_assertions,
)
from mcptest.cli.scaffold import ScaffoldError, scaffold_project
from mcptest.diff import BaselineStore, diff_traces
from mcptest.fixtures.loader import FixtureLoadError, load_fixture
from mcptest.registry import InstallError, install_pack, list_packs, PACKS
from mcptest.runner import RetryResult, Runner, RunnerError, SubprocessAdapter, Trace
from mcptest.testspec import (
    TestCase,
    TestSuite,
    TestSuiteLoadError,
    load_test_suite,
)
from mcptest.testspec.loader import discover_test_files


# ---------------------------------------------------------------------------
# Result types — one per assertion evaluated + per test case run
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    suite_name: str
    case_name: str
    trace: Trace
    assertion_results: list[AssertionResult]
    error: str | None = None
    metrics: list[Any] = field(default_factory=list)  # list[MetricResult]
    retry_result: RetryResult | None = None

    @property
    def passed(self) -> bool:
        if self.error is not None:
            return False
        # When multi-attempt retry data is present, use its aggregate verdict.
        if self.retry_result is not None:
            return self.retry_result.passed
        return (
            self.trace.succeeded
            and all(r.passed for r in self.assertion_results)
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "suite": self.suite_name,
            "case": self.case_name,
            "passed": self.passed,
            "error": self.error,
            "trace": self.trace.to_dict(),
            "assertions": [r.to_dict() for r in self.assertion_results],
            "metrics": [m.to_dict() for m in self.metrics],
        }
        if self.retry_result is not None:
            d["retry"] = self.retry_result.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaseResult:
        """Reconstruct a CaseResult from its ``to_dict()`` representation."""
        from mcptest.metrics.base import MetricResult

        assertion_results = [
            AssertionResult(
                passed=a["passed"],
                name=a["name"],
                message=a["message"],
                details=a.get("details", {}),
            )
            for a in data.get("assertions", [])
        ]
        metrics = [
            MetricResult(
                name=m["name"],
                score=m["score"],
                label=m["label"],
                details=m.get("details", {}),
            )
            for m in data.get("metrics", [])
        ]
        retry_result: RetryResult | None = None
        if "retry" in data:
            retry_result = RetryResult.from_dict(data["retry"])
        return cls(
            suite_name=data["suite"],
            case_name=data["case"],
            trace=Trace.from_dict(data.get("trace", {})),
            assertion_results=assertion_results,
            error=data.get("error"),
            metrics=metrics,
            retry_result=retry_result,
        )


# ---------------------------------------------------------------------------
# init — scaffold a new project
# ---------------------------------------------------------------------------


@click.command(help="Scaffold a new mcptest project in DIR (default: current dir).")
@click.argument(
    "path",
    default=".",
    type=click.Path(file_okay=False, resolve_path=True),
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing scaffold files if they exist.",
)
def init_command(path: str, force: bool) -> None:
    console = Console()
    try:
        created = scaffold_project(Path(path), force=force)
    except ScaffoldError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    console.print(f"[green]✓[/green] Scaffolded project at [bold]{path}[/bold]")
    for rel in created:
        console.print(f"  [dim]created[/dim] {rel}")
    console.print(
        "\nNext steps:\n"
        "  1. Edit [bold]fixtures/example.yaml[/bold] to describe your mock server\n"
        "  2. Edit [bold]tests/test_example.yaml[/bold] to define your cases\n"
        "  3. Run [bold cyan]mcptest run[/bold cyan] to execute your tests\n"
    )


# ---------------------------------------------------------------------------
# run — discover and execute test files
# ---------------------------------------------------------------------------


@click.command(help="Run test files under PATH (default: tests/).")
@click.argument(
    "path",
    default="tests",
    type=click.Path(exists=False, resolve_path=True),
)
@click.option("--ci", is_flag=True, help="Exit non-zero on any failure.")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON on stdout (equivalent to --format json).",
)
@click.option(
    "--format",
    "format_",
    type=click.Choice(["table", "json", "junit", "tap", "html"]),
    default="table",
    help="Output format: table (default), json, junit (JUnit XML), tap (TAP v14), or html.",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default=None,
    help="Write output to this file (required for html; defaults to mcptest-report.html).",
)
@click.option(
    "--fail-fast",
    is_flag=True,
    help="Stop at the first failing case.",
)
@click.option(
    "--retry",
    "retry_override",
    default=None,
    type=int,
    help="Override retry count for every case (must be >= 1).",
)
@click.option(
    "--tolerance",
    "tolerance_override",
    default=None,
    type=float,
    help="Override pass-rate tolerance for every case (0.0–1.0).",
)
@click.option(
    "-j",
    "--parallel",
    "parallel_workers",
    default=1,
    type=int,
    help="Run cases in parallel (0 = auto-detect CPU count, 1 = serial).",
)
def run_command(
    path: str,
    ci: bool,
    json_output: bool,
    format_: str,
    output_path: str | None,
    fail_fast: bool,
    retry_override: int | None,
    tolerance_override: float | None,
    parallel_workers: int,
) -> None:
    # Backwards compat: --json flag is equivalent to --format json.
    if json_output:
        format_ = "json"

    console = Console(stderr=(format_ != "table"))

    files = discover_test_files(path)
    if not files:
        console.print(f"[yellow]no test files found under[/yellow] {path}")
        return

    use_parallel = parallel_workers != 1

    def _progress(r: CaseResult) -> None:
        if format_ != "table":
            return
        if r.case_name == "<load>" and r.error:
            # Load errors are always shown inline so the filename is visible
            # (the table truncates long paths).
            console.print(f"[red]× {r.suite_name}[/red] {r.error}")
        elif use_parallel:
            color = "green" if r.passed else "red"
            label = "PASS" if r.passed else "FAIL"
            console.print(
                f"  [{color}]{label}[/{color}] {r.suite_name} > {r.case_name}"
            )

    wall_start = time.perf_counter()
    all_results = execute_test_files(
        files,
        parallel_workers=parallel_workers,
        fail_fast=fail_fast,
        retry_override=retry_override,
        tolerance_override=tolerance_override,
        on_result=_progress,
    )
    wall_clock_s = time.perf_counter() - wall_start
    total_cpu_s = sum(r.trace.duration_s for r in all_results)

    if format_ == "json":
        # Aggregate per-metric averages across all cases.
        metric_totals: dict[str, list[float]] = {}
        for r in all_results:
            for m in r.metrics:
                metric_totals.setdefault(m.name, []).append(m.score)
        metric_summary = {
            name: sum(scores) / len(scores)
            for name, scores in metric_totals.items()
        }
        # Resolve effective worker count (mirrors run_cases_parallel logic).
        import os as _os

        if use_parallel:
            effective_workers = parallel_workers
            if effective_workers == 0:
                effective_workers = min(
                    _os.cpu_count() or 1, len(all_results)
                )
        else:
            effective_workers = 1

        speedup = total_cpu_s / wall_clock_s if wall_clock_s > 0 else 1.0
        payload = {
            "passed": sum(1 for r in all_results if r.passed),
            "failed": sum(1 for r in all_results if not r.passed),
            "total": len(all_results),
            "cases": [r.to_dict() for r in all_results],
            "metric_summary": metric_summary,
            "parallel": {
                "workers": effective_workers,
                "wall_clock_s": round(wall_clock_s, 3),
                "total_cpu_s": round(total_cpu_s, 3),
                "speedup": round(speedup, 2),
            },
        }
        click.echo(json_module.dumps(payload, indent=2, default=str))
    elif format_ in ("junit", "tap"):
        from mcptest.exporters import get_exporter

        click.echo(get_exporter(format_).export(all_results))
    elif format_ == "html":
        from mcptest.exporters import get_exporter

        dest = output_path or "mcptest-report.html"
        html_content = get_exporter("html").export(all_results)
        Path(dest).write_text(html_content, encoding="utf-8")
        click.echo(f"HTML report written to {dest}", err=True)
    else:
        _render_results(console, all_results, wall_clock_s=wall_clock_s,
                        total_cpu_s=total_cpu_s, parallel_workers=parallel_workers
                        if use_parallel else 1)

    failed = sum(1 for r in all_results if not r.passed)
    if failed and ci:
        sys.exit(1)


def _build_suite_work(
    suite: TestSuite,
    source: Path,
) -> tuple[list[Any], CaseResult | None]:
    """Build CaseWork items for a suite without executing any cases.

    Returns ``(work_items, None)`` on success, or ``([], error_result)`` when
    runner setup fails (missing fixture, bad agent spec, etc.).

    Importing ``CaseWork`` lazily here avoids a circular-import: the parallel
    module imports ``_run_case`` from this module, while this module imports
    ``CaseWork`` from the parallel module.
    """
    from mcptest.runner.parallel import CaseWork  # noqa: PLC0415

    base_dir = source.parent
    fixture_paths = suite.resolve_fixtures(base_dir)

    try:
        adapter = suite.agent.build_adapter(base_dir)
        runner = Runner(fixtures=fixture_paths, agent=adapter)
    except (RunnerError, FixtureLoadError, ValueError) as exc:
        return [], CaseResult(
            suite_name=suite.name,
            case_name="<setup>",
            trace=Trace(),
            assertion_results=[],
            error=str(exc),
        )

    return [CaseWork(suite=suite, case=case, runner=runner) for case in suite.cases], None


def _iter_suite_results(
    suite: TestSuite,
    source: Path,
    *,
    retry_override: int | None = None,
    tolerance_override: float | None = None,
):
    """Yield one CaseResult per case in order, lazily.

    Yielding instead of building a list lets the top-level runner honour
    ``--fail-fast`` without running every remaining case first.
    """
    work_items, setup_error = _build_suite_work(suite, source)
    if setup_error is not None:
        yield setup_error
        return

    for w in work_items:
        yield _run_case(
            w.runner,
            w.suite,
            w.case,
            retry_override=retry_override,
            tolerance_override=tolerance_override,
        )


def _run_case(
    runner: Runner,
    suite: TestSuite,
    case: TestCase,
    *,
    retry_override: int | None = None,
    tolerance_override: float | None = None,
) -> CaseResult:
    retry = retry_override if retry_override is not None else case.retry
    tolerance = tolerance_override if tolerance_override is not None else case.tolerance

    try:
        assertions = parse_assertions(case.assertions)
    except ValueError as exc:
        return CaseResult(
            suite_name=suite.name,
            case_name=case.name,
            trace=Trace(input=case.input),
            assertion_results=[],
            error=f"assertion parse error: {exc}",
        )

    from mcptest.metrics import compute_all as _compute_all

    if retry == 1:
        # Fast path — identical to original behaviour.
        try:
            trace = runner.run(case.input)
        except Exception as exc:  # pragma: no cover - defensive
            return CaseResult(
                suite_name=suite.name,
                case_name=case.name,
                trace=Trace(input=case.input),
                assertion_results=[],
                error=str(exc),
            )
        results = check_all(assertions, trace)
        metric_results = _compute_all(trace)
        return CaseResult(
            suite_name=suite.name,
            case_name=case.name,
            trace=trace,
            assertion_results=results,
            metrics=metric_results,
        )

    # Multi-attempt path — run N times, evaluate assertions on each attempt.
    def _evaluate(trace: Trace) -> bool:
        if not trace.succeeded:
            return False
        return all(r.passed for r in check_all(assertions, trace))

    retry_result = runner.run_with_retry(
        case.input,
        retry=retry,
        tolerance=tolerance,
        evaluate=_evaluate,
    )

    # Use the last trace as the "representative" for metrics / exporters.
    representative_trace = retry_result.traces[-1]
    # Inject retry_result into the representative trace's metadata so that the
    # stability metric (and any future metric) can read per-attempt data.
    representative_trace.metadata["retry_result"] = retry_result.to_dict()
    # Assertion results from the last attempt.
    last_assertion_results = check_all(assertions, representative_trace)
    metric_results = _compute_all(representative_trace)

    return CaseResult(
        suite_name=suite.name,
        case_name=case.name,
        trace=representative_trace,
        assertion_results=last_assertion_results,
        metrics=metric_results,
        retry_result=retry_result,
    )


def execute_test_files(
    files: list[Path],
    *,
    parallel_workers: int = 1,
    fail_fast: bool = False,
    retry_override: int | None = None,
    tolerance_override: float | None = None,
    on_result: Callable[[CaseResult], None] | None = None,
) -> list[CaseResult]:
    """Run a list of test suite files and return all :class:`CaseResult` objects.

    Shared between :func:`run_command` and :class:`~mcptest.watch.WatchEngine`.
    Does not write any output; use the *on_result* callback for live progress.
    """
    all_results: list[CaseResult] = []
    stop = False
    use_parallel = parallel_workers != 1

    if use_parallel:
        from mcptest.runner.parallel import ParallelConfig, run_cases_parallel  # noqa: PLC0415

        for test_file in files:
            if stop:
                break
            try:
                suite = load_test_suite(test_file)
            except TestSuiteLoadError as exc:
                r = CaseResult(
                    suite_name=str(test_file),
                    case_name="<load>",
                    trace=Trace(),
                    assertion_results=[],
                    error=str(exc),
                )
                all_results.append(r)
                if on_result:
                    on_result(r)
                if fail_fast:
                    stop = True
                continue

            work_items, setup_error = _build_suite_work(suite, test_file)
            if setup_error is not None:
                all_results.append(setup_error)
                if on_result:
                    on_result(setup_error)
                if fail_fast:
                    stop = True
                continue

            if suite.parallel:
                config = ParallelConfig(
                    max_workers=parallel_workers, fail_fast=fail_fast
                )
                suite_results = run_cases_parallel(
                    work_items,
                    config,
                    retry_override=retry_override,
                    tolerance_override=tolerance_override,
                    on_result=on_result,
                )
            else:
                # Suite opts out of parallelism — run its cases serially.
                suite_results = []
                for w in work_items:
                    r = _run_case(
                        w.runner,
                        w.suite,
                        w.case,
                        retry_override=retry_override,
                        tolerance_override=tolerance_override,
                    )
                    suite_results.append(r)
                    if on_result:
                        on_result(r)
                    if fail_fast and not r.passed:
                        stop = True
                        break

            all_results.extend(suite_results)
            if fail_fast and any(not r.passed for r in suite_results) and not stop:
                stop = True

    else:
        # Serial path.
        for test_file in files:
            if stop:
                break
            try:
                suite = load_test_suite(test_file)
            except TestSuiteLoadError as exc:
                r = CaseResult(
                    suite_name=str(test_file),
                    case_name="<load>",
                    trace=Trace(),
                    assertion_results=[],
                    error=str(exc),
                )
                all_results.append(r)
                if on_result:
                    on_result(r)
                if fail_fast:
                    stop = True
                continue

            for case_result in _iter_suite_results(
                suite,
                test_file,
                retry_override=retry_override,
                tolerance_override=tolerance_override,
            ):
                all_results.append(case_result)
                if on_result:
                    on_result(case_result)
                if fail_fast and not case_result.passed:
                    stop = True
                    break

    return all_results


def _render_results(
    console: Console,
    results: list[CaseResult],
    *,
    wall_clock_s: float = 0.0,
    total_cpu_s: float = 0.0,
    parallel_workers: int = 1,
) -> None:
    table = Table(title="mcptest results", show_lines=False)
    table.add_column("Suite")
    table.add_column("Case")
    table.add_column("Status", justify="center")
    table.add_column("Details")

    for r in results:
        if r.passed:
            status = "[green]PASS[/green]"
        else:
            status = "[red]FAIL[/red]"

        details_parts: list[str] = []
        if r.error:
            details_parts.append(f"error: {r.error}")
        for a in r.assertion_results:
            marker = "✓" if a.passed else "✗"
            color = "green" if a.passed else "red"
            details_parts.append(f"[{color}]{marker}[/{color}] {a.name}: {a.message}")

        table.add_row(r.suite_name, r.case_name, status, "\n".join(details_parts) or "—")

    console.print(table)

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    console.print(
        f"\n[bold]{passed} passed[/bold], [bold red]{failed} failed[/bold red] ({len(results)} total)"
    )

    # Timing / speedup footer.
    if wall_clock_s > 0:
        if parallel_workers != 1 and total_cpu_s > 0:
            speedup = total_cpu_s / wall_clock_s
            console.print(
                f"[dim]⏱  {wall_clock_s:.2f}s wall "
                f"({total_cpu_s:.2f}s total, {speedup:.1f}× speedup "
                f"with -j {parallel_workers})[/dim]"
            )
        else:
            console.print(f"[dim]⏱  {wall_clock_s:.2f}s[/dim]")

    # Metric summary line (averaged across all cases that have metrics).
    metric_totals: dict[str, list[float]] = {}
    for r in results:
        for m in r.metrics:
            metric_totals.setdefault(m.name, []).append(m.score)
    if metric_totals:
        parts: list[str] = []
        for name in sorted(metric_totals):
            avg = sum(metric_totals[name]) / len(metric_totals[name])
            color = "green" if avg >= 0.8 else "yellow" if avg >= 0.5 else "red"
            parts.append(f"[{color}]{name}: {avg:.2f}[/{color}]")
        console.print("Metrics: " + "  ".join(parts))


# ---------------------------------------------------------------------------
# validate — check fixture + test YAML files without running agents
# ---------------------------------------------------------------------------


@click.command(help="Validate fixtures/ and tests/ YAML without running any agent.")
@click.argument(
    "path",
    default=".",
    type=click.Path(exists=False, resolve_path=True),
)
def validate_command(path: str) -> None:
    console = Console()
    root = Path(path)
    errors: list[str] = []
    checked = 0

    fixture_dir = root / "fixtures"
    if fixture_dir.exists():
        for f in sorted(fixture_dir.glob("**/*.yaml")) + sorted(
            fixture_dir.glob("**/*.yml")
        ):
            checked += 1
            try:
                load_fixture(f)
                console.print(f"[green]✓[/green] fixture {f}")
            except FixtureLoadError as exc:
                errors.append(f"{f}: {exc}")
                console.print(f"[red]×[/red] fixture {f}: {exc}")

    test_files = discover_test_files(root / "tests")
    for t in test_files:
        checked += 1
        try:
            suite = load_test_suite(t)
            parse_assertions(
                [a for case in suite.cases for a in case.assertions]
            )
            console.print(f"[green]✓[/green] test   {t}")
        except (TestSuiteLoadError, ValueError) as exc:
            errors.append(f"{t}: {exc}")
            console.print(f"[red]×[/red] test   {t}: {exc}")

    if checked == 0:
        console.print("[yellow]nothing to validate[/yellow]")
        return

    if errors:
        console.print(f"\n[red]{len(errors)} error(s)[/red] in {checked} file(s)")
        sys.exit(1)
    console.print(f"\n[green]all {checked} file(s) OK[/green]")


# ---------------------------------------------------------------------------
# record — run an agent and save its trace as a baseline
# ---------------------------------------------------------------------------


@click.command(help="Run AGENT_COMMAND once against fixtures and save its trace.")
@click.argument("agent_command")
@click.option(
    "--fixture",
    "fixture_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Fixture YAML to run against (repeatable).",
)
@click.option(
    "--input",
    "input_text",
    default="",
    help="Input passed to the agent via stdin.",
)
@click.option(
    "--output",
    "output_path",
    default="recording.json",
    type=click.Path(dir_okay=False),
    help="Where to write the recorded trace.",
)
def record_command(
    agent_command: str,
    fixture_paths: tuple[str, ...],
    input_text: str,
    output_path: str,
) -> None:
    console = Console()
    import shlex

    parts = shlex.split(agent_command)
    if not parts:
        console.print("[red]error:[/red] empty agent command")
        sys.exit(1)
    command, *args = parts
    if command == "python":
        command = sys.executable

    adapter = SubprocessAdapter(command=command, args=args)
    try:
        runner = Runner(fixtures=list(fixture_paths), agent=adapter)
    except (FixtureLoadError, RunnerError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    trace = runner.run(input_text)
    trace.save(output_path)
    console.print(
        f"[green]✓[/green] recorded trace to [bold]{output_path}[/bold] "
        f"({trace.total_tool_calls} tool call(s), exit={trace.exit_code})"
    )


# ---------------------------------------------------------------------------
# snapshot / diff — trajectory regression gating
# ---------------------------------------------------------------------------


def _run_all_cases(path: str) -> list[tuple[str, str, Trace]]:
    """Run every test case under `path` and return `(suite, case, trace)` triples.

    Assertion failures are *not* treated as errors here — snapshot/diff are
    interested in the raw trajectory, not in whether the run already passes
    its assertions.
    """
    files = discover_test_files(path)
    out: list[tuple[str, str, Trace]] = []
    for f in files:
        try:
            suite = load_test_suite(f)
        except TestSuiteLoadError:
            continue
        base_dir = f.parent
        fixture_paths = suite.resolve_fixtures(base_dir)
        try:
            adapter = suite.agent.build_adapter(base_dir)
            runner = Runner(fixtures=fixture_paths, agent=adapter)
        except (FixtureLoadError, RunnerError, ValueError):
            continue
        for case in suite.cases:
            trace = runner.run(case.input)
            out.append((suite.name, case.name, trace))
    return out


@click.command(help="Run tests under PATH and save each trace as a baseline.")
@click.argument(
    "path",
    default="tests",
    type=click.Path(exists=False, resolve_path=True),
)
@click.option(
    "--baseline-dir",
    default=".mcptest/baselines",
    type=click.Path(file_okay=False),
    help="Where to write baseline files.",
)
@click.option(
    "--update",
    is_flag=True,
    help="Overwrite existing baselines (otherwise existing baselines are kept).",
)
def snapshot_command(path: str, baseline_dir: str, update: bool) -> None:
    console = Console()
    store = BaselineStore(baseline_dir)
    store.ensure()

    cases = _run_all_cases(path)
    if not cases:
        console.print(f"[yellow]no test files found under[/yellow] {path}")
        return

    saved = 0
    skipped = 0
    for suite_name, case_name, trace in cases:
        if store.exists(suite_name, case_name) and not update:
            console.print(
                f"[dim]- skipped {suite_name}::{case_name}[/dim] (use --update to overwrite)"
            )
            skipped += 1
            continue
        store.save(suite_name, case_name, trace)
        console.print(
            f"[green]✓[/green] saved baseline for {suite_name}::{case_name} "
            f"({trace.total_tool_calls} tool call(s))"
        )
        saved += 1

    console.print(f"\n[bold]{saved} saved[/bold], {skipped} skipped")


@click.command(help="Run tests under PATH and diff each trace against its baseline.")
@click.argument(
    "path",
    default="tests",
    type=click.Path(exists=False, resolve_path=True),
)
@click.option(
    "--baseline-dir",
    default=".mcptest/baselines",
    type=click.Path(file_okay=False),
    help="Directory containing baseline trace files.",
)
@click.option(
    "--latency-threshold-pct",
    default=50.0,
    type=float,
    help="Report latency regressions above this percentage.",
)
@click.option("--ci", is_flag=True, help="Exit non-zero if any regression is found.")
def diff_command(
    path: str,
    baseline_dir: str,
    latency_threshold_pct: float,
    ci: bool,
) -> None:
    console = Console()
    store = BaselineStore(baseline_dir)

    cases = _run_all_cases(path)
    if not cases:
        console.print(f"[yellow]no test files found under[/yellow] {path}")
        return

    total_regressions = 0
    missing_baselines = 0

    for suite_name, case_name, trace in cases:
        baseline = store.load(suite_name, case_name)
        header = f"{suite_name}::{case_name}"
        if baseline is None:
            console.print(f"[yellow]? {header}[/yellow] no baseline on disk")
            missing_baselines += 1
            continue

        diff = diff_traces(
            baseline, trace, latency_threshold_pct=latency_threshold_pct
        )
        if not diff.has_regressions:
            console.print(f"[green]✓ {header}[/green] no regressions")
            continue

        total_regressions += len(diff.regressions)
        console.print(f"[red]× {header}[/red] {len(diff.regressions)} regression(s)")
        for r in diff.regressions:
            console.print(f"    [red]{r.kind}[/red]: {r.message}")

    console.print(
        f"\n[bold]{total_regressions} regression(s)[/bold] across {len(cases)} case(s)"
        + (f", {missing_baselines} missing baseline(s)" if missing_baselines else "")
    )

    if ci and (total_regressions or missing_baselines):
        sys.exit(1)


# ---------------------------------------------------------------------------
# install-pack / list-packs — the registry of pre-built test packs
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# metrics — compute quantitative quality scores from a saved trace
# ---------------------------------------------------------------------------


@click.command(help="Compute quantitative quality metrics from a saved trace JSON file.")
@click.argument(
    "trace_json",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--fixture",
    "fixture_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Fixture YAML for schema_compliance and tool_coverage metrics (repeatable).",
)
@click.option(
    "--reference",
    "reference_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Baseline trace JSON for trajectory_similarity metric.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON on stdout instead of a human-friendly table.",
)
def metrics_command(
    trace_json: str,
    fixture_paths: tuple[str, ...],
    reference_path: str | None,
    json_output: bool,
) -> None:
    from mcptest.metrics import compute_all

    console = Console(stderr=json_output)

    try:
        trace = Trace.load(trace_json)
    except Exception as exc:
        console.print(f"[red]error:[/red] could not load trace: {exc}")
        sys.exit(1)

    fixtures = []
    for fp in fixture_paths:
        try:
            fixtures.append(load_fixture(fp))
        except FixtureLoadError as exc:
            console.print(f"[red]error:[/red] could not load fixture {fp}: {exc}")
            sys.exit(1)

    reference = None
    if reference_path is not None:
        try:
            reference = Trace.load(reference_path)
        except Exception as exc:
            console.print(f"[red]error:[/red] could not load reference trace: {exc}")
            sys.exit(1)

    results = compute_all(trace, reference=reference, fixtures=fixtures or None)

    if json_output:
        click.echo(json_module.dumps([r.to_dict() for r in results], indent=2, default=str))
        return

    table = Table(title="mcptest metrics", show_lines=False)
    table.add_column("Metric")
    table.add_column("Score", justify="right")
    table.add_column("Label")
    table.add_column("Details")

    for r in results:
        if r.score >= 0.8:
            score_str = f"[green]{r.score:.3f}[/green]"
        elif r.score >= 0.5:
            score_str = f"[yellow]{r.score:.3f}[/yellow]"
        else:
            score_str = f"[red]{r.score:.3f}[/red]"

        details_str = ", ".join(
            f"{k}: {v}"
            for k, v in r.details.items()
            if k != "note" or len(r.details) == 1
        )
        if "note" in r.details and len(r.details) > 1:
            details_str = r.details["note"] + (f", {details_str}" if details_str else "")

        table.add_row(r.name, score_str, r.label, details_str or "—")

    console.print(table)


# ---------------------------------------------------------------------------
# compare — diff two trace JSON files by metric scores
# ---------------------------------------------------------------------------


def _render_comparison(console: Console, report: Any) -> None:
    """Render a ComparisonReport to the Rich console as a table."""
    table = Table(title="mcptest compare", show_lines=False)
    table.add_column("Metric")
    table.add_column("Base", justify="right")
    table.add_column("Head", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Status", justify="center")

    for delta in report.deltas:
        if delta.regressed:
            status = "[red]REGRESSED[/red]"
            delta_str = f"[red]{delta.delta:+.3f}[/red]"
        elif delta.delta >= 0.05:
            status = "[green]IMPROVED[/green]"
            delta_str = f"[green]{delta.delta:+.3f}[/green]"
        else:
            status = "[dim]stable[/dim]"
            delta_str = f"[dim]{delta.delta:+.3f}[/dim]"

        table.add_row(
            delta.name,
            f"{delta.base_score:.3f}",
            f"{delta.head_score:.3f}",
            delta_str,
            status,
        )

    console.print(table)
    regressions = len(report.regressions)
    improvements = len(report.improvements)
    console.print(
        f"\n[bold]{regressions} regression(s)[/bold], {improvements} improvement(s)"
        + (" — [red]FAILED[/red]" if not report.overall_passed else " — [green]OK[/green]")
    )


@click.command(help="Compare two trace JSON files and report metric regressions.")
@click.argument(
    "base_trace",
    type=click.Path(exists=True, dir_okay=False),
)
@click.argument(
    "head_trace",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--threshold",
    default=0.1,
    type=float,
    help="Regression threshold applied to all metrics (default: 0.1).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON on stdout instead of a human-friendly table.",
)
@click.option(
    "--ci",
    is_flag=True,
    help="Exit non-zero if any metric regressed beyond the threshold.",
)
def compare_command(
    base_trace: str,
    head_trace: str,
    threshold: float,
    json_output: bool,
    ci: bool,
) -> None:
    from mcptest.compare import DEFAULT_THRESHOLDS, compare_traces

    console = Console(stderr=json_output)

    try:
        base = Trace.load(base_trace)
    except Exception as exc:
        console.print(f"[red]error:[/red] could not load base trace: {exc}")
        sys.exit(1)
    try:
        head = Trace.load(head_trace)
    except Exception as exc:
        console.print(f"[red]error:[/red] could not load head trace: {exc}")
        sys.exit(1)

    # Apply the single CLI threshold to every known metric.
    thresholds = {name: threshold for name in DEFAULT_THRESHOLDS}
    report = compare_traces(base, head, thresholds=thresholds)

    if json_output:
        click.echo(json_module.dumps(report.to_dict(), indent=2, default=str))
    else:
        _render_comparison(console, report)

    if ci and not report.overall_passed:
        sys.exit(1)


@click.command(help="List the pre-built test packs that ship with mcptest.")
def list_packs_command() -> None:
    console = Console()
    for name in list_packs():
        pack = PACKS[name]
        console.print(f"[bold]{name}[/bold] — {pack.description}")


@click.command(help="Install a pre-built test pack into PATH (default: current dir).")
@click.argument("name")
@click.argument(
    "path",
    default=".",
    type=click.Path(file_okay=False, resolve_path=True),
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing files if they conflict with the pack.",
)
def install_pack_command(name: str, path: str, force: bool) -> None:
    console = Console()
    try:
        written = install_pack(name, Path(path), force=force)
    except InstallError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    console.print(
        f"[green]✓[/green] installed pack [bold]{name}[/bold] into {path}"
    )
    for rel in written:
        console.print(f"  [dim]created[/dim] {rel}")


# ---------------------------------------------------------------------------
# export — convert a saved mcptest JSON result file to another CI format
# ---------------------------------------------------------------------------


@click.command(help="Convert a mcptest JSON result file to another CI format.")
@click.argument("run_json", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--format",
    "format_",
    type=click.Choice(["junit", "tap", "html"]),
    required=True,
    help="Output format: junit (JUnit XML), tap (TAP v14), or html.",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default=None,
    help="Write output to this file (for html; defaults to mcptest-report.html).",
)
def export_command(run_json: str, format_: str, output_path: str | None) -> None:
    from mcptest.exporters import get_exporter

    console = Console()
    try:
        data = json_module.loads(Path(run_json).read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]error:[/red] could not read {run_json}: {exc}")
        sys.exit(1)

    try:
        results = [CaseResult.from_dict(c) for c in data.get("cases", [])]
    except Exception as exc:
        console.print(f"[red]error:[/red] could not parse results: {exc}")
        sys.exit(1)

    if format_ == "html":
        dest = output_path or "mcptest-report.html"
        Path(dest).write_text(get_exporter("html").export(results), encoding="utf-8")
        click.echo(f"HTML report written to {dest}", err=True)
    else:
        click.echo(get_exporter(format_).export(results))


@click.command(
    name="cloud-push",
    help=(
        "Push a trace JSON file to the mcptest cloud backend, compute metrics,"
        " and optionally auto-compare against the baseline."
    ),
)
@click.argument("trace_json", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--url",
    envvar="MCPTEST_CLOUD_URL",
    default="http://localhost:8000",
    show_default=True,
    help="Base URL of the mcptest cloud backend.",
)
@click.option("--suite", default=None, help="Suite name to tag this run.")
@click.option("--case", default=None, help="Case name to tag this run.")
@click.option("--branch", default=None, help="Git branch label.")
@click.option("--git-sha", default=None, help="Git SHA label.")
@click.option("--git-ref", default=None, help="Git ref label.")
@click.option("--environment", default=None, help="Deployment environment label.")
@click.option(
    "--check",
    is_flag=True,
    help="Auto-compare against the baseline after pushing.",
)
@click.option(
    "--promote",
    is_flag=True,
    help="Promote this run as the new baseline after pushing.",
)
@click.option(
    "--ci",
    is_flag=True,
    help="Exit non-zero if regression detected (requires --check).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON on stdout.",
)
@click.option(
    "--api-key",
    "api_key",
    envvar="MCPTEST_API_KEY",
    default=None,
    help="API key sent as X-API-Key header (env: MCPTEST_API_KEY).",
)
def cloud_push_command(
    trace_json: str,
    url: str,
    suite: str | None,
    case: str | None,
    branch: str | None,
    git_sha: str | None,
    git_ref: str | None,
    environment: str | None,
    check: bool,
    promote: bool,
    ci: bool,
    json_output: bool,
    api_key: str | None,
) -> None:
    import urllib.error
    import urllib.request

    from mcptest.metrics import compute_all

    console = Console(stderr=json_output)

    # Load and validate the trace.
    try:
        trace = Trace.load(trace_json)
    except Exception as exc:
        console.print(f"[red]error:[/red] could not load trace: {exc}")
        sys.exit(1)

    # Compute metrics from the trace.
    metric_results = compute_all(trace)
    metric_scores = {r.name: r.score for r in metric_results}

    # Build the POST /runs payload.
    payload: dict[str, Any] = {
        "trace_id": trace.trace_id,
        "suite": suite,
        "case": case,
        "input": trace.input or "",
        "output": trace.output or "",
        "exit_code": trace.exit_code,
        "duration_s": trace.duration_s,
        "total_tool_calls": len(trace.tool_calls),
        "passed": trace.succeeded,
        "tool_calls": [tc.to_dict() for tc in trace.tool_calls],
        "run_metadata": {},
        "metric_scores": metric_scores,
        "branch": branch,
        "git_sha": git_sha,
        "git_ref": git_ref,
        "environment": environment,
    }

    def _headers() -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if api_key:
            h["X-API-Key"] = api_key
        return h

    # POST /runs
    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/runs",
            data=json_module.dumps(payload).encode(),
            headers=_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            run_data = json_module.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        console.print(f"[red]error:[/red] POST /runs failed ({exc.code}): {body}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]error:[/red] could not reach cloud backend: {exc}")
        sys.exit(1)

    run_id: int = run_data["id"]
    if json_output:
        result: dict[str, Any] = {"run": run_data}
    else:
        console.print(f"[green]✓[/green] pushed run [bold]#{run_id}[/bold] to {url}")

    # Optional: promote as baseline.
    if promote:
        try:
            req = urllib.request.Request(
                f"{url.rstrip('/')}/runs/{run_id}/promote",
                data=b"",
                headers=_headers(),
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                promote_data = json_module.loads(resp.read())
            if json_output:
                result["promote"] = promote_data
            else:
                console.print(f"[green]✓[/green] promoted run #{run_id} as baseline")
        except Exception as exc:
            console.print(f"[yellow]warning:[/yellow] could not promote baseline: {exc}")

    # Optional: auto-compare against baseline.
    if check:
        try:
            req = urllib.request.Request(
                f"{url.rstrip('/')}/runs/{run_id}/check",
                data=b"",
                headers=_headers(),
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                check_data = json_module.loads(resp.read())
        except Exception as exc:
            console.print(f"[yellow]warning:[/yellow] could not run check: {exc}")
            check_data = None

        if check_data:
            if json_output:
                result["check"] = check_data
            else:
                status = check_data.get("status", "unknown")
                if status == "no_baseline":
                    console.print("[dim]no baseline found — skipping regression check[/dim]")
                elif status == "pass":
                    console.print("[green]✓[/green] no regressions detected")
                else:
                    rc = check_data.get("regression_count", 0)
                    console.print(f"[red]✗[/red] {rc} regression(s) detected")
                    for d in check_data.get("deltas", []):
                        if d.get("regressed"):
                            console.print(
                                f"  [red]{d['name']}[/red]: "
                                f"{d['base_score']:.3f} → {d['head_score']:.3f} "
                                f"(Δ {d['delta']:+.3f})"
                            )

            if ci and check_data.get("status") == "fail":
                if json_output:
                    click.echo(json_module.dumps(result, indent=2, default=str))
                sys.exit(1)

    if json_output:
        click.echo(json_module.dumps(result, indent=2, default=str))


# ---------------------------------------------------------------------------
# generate — schema-driven test suite generator
# ---------------------------------------------------------------------------


@click.command(help="Generate a test suite YAML from fixture input_schema declarations.")
@click.argument(
    "fixture_paths",
    nargs=-1,
    type=click.Path(exists=True, resolve_path=True),
)
@click.option(
    "--agent",
    "agent_cmd",
    required=True,
    help="Agent command, e.g. 'python agent.py'.",
)
@click.option(
    "--name",
    default=None,
    help="Suite name (default: auto-derived from the first fixture file name).",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default=None,
    help="Write output to this YAML file (default: stdout).",
)
@click.option(
    "--categories",
    default=None,
    help=(
        "Comma-separated list of categories to generate: "
        "happy,match,type_error,missing,edge,error (default: all)."
    ),
)
@click.option(
    "--timeout",
    "timeout_s",
    type=float,
    default=60.0,
    show_default=True,
    help="Agent timeout in seconds.",
)
def generate_command(
    fixture_paths: tuple[str, ...],
    agent_cmd: str,
    name: str | None,
    output_path: str | None,
    categories: str | None,
    timeout_s: float,
) -> None:
    from mcptest.generate import generate_suite

    console = Console(stderr=True)

    if not fixture_paths:
        console.print("[red]error:[/red] at least one FIXTURE_PATH is required")
        sys.exit(1)

    fixtures = []
    for fp in fixture_paths:
        try:
            fixtures.append(load_fixture(fp))
        except FixtureLoadError as exc:
            console.print(f"[red]error:[/red] {exc}")
            sys.exit(1)

    # Derive a suite name from the first fixture file when not supplied.
    if name is None:
        stem = Path(fixture_paths[0]).stem
        name = f"{stem}-generated"

    # Parse categories option.
    parsed_categories: list[str] | None = None
    if categories:
        parsed_categories = [c.strip() for c in categories.split(",") if c.strip()]

    try:
        suite_dict = generate_suite(
            fixtures,
            name=name,
            agent_cmd=agent_cmd,
            categories=parsed_categories,
            timeout_s=timeout_s,
            fixture_paths=list(fixture_paths),
        )
    except ValueError as exc:
        console.print(f"[red]error:[/red] {exc}")
        sys.exit(1)

    output_yaml = yaml.dump(suite_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)

    if output_path:
        try:
            Path(output_path).write_text(output_yaml, encoding="utf-8")
        except OSError as exc:
            console.print(f"[red]error:[/red] could not write {output_path}: {exc}")
            sys.exit(1)
        console.print(
            f"[green]✓[/green] wrote [bold]{len(suite_dict['cases'])}[/bold] "
            f"cases to [bold]{output_path}[/bold]"
        )
    else:
        click.echo(output_yaml, nl=False)


# ---------------------------------------------------------------------------
# coverage — fixture surface area coverage analysis
# ---------------------------------------------------------------------------


@click.command(
    help=(
        "Analyse fixture surface area coverage from a mcptest results JSON file.\n\n"
        "Loads fixtures, extracts traces from RESULTS_JSON (a full mcptest run "
        "output or a single trace JSON), and reports which tool responses and "
        "error scenarios were exercised.\n\n"
        "Use --suite to supply test suite YAML files so that inject_error "
        "declarations are included in the error coverage count.\n\n"
        "Exit code is 1 if overall_score < --threshold (default: always 0)."
    )
)
@click.argument(
    "results_json",
    required=False,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--fixture",
    "fixture_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Fixture YAML to analyse (repeatable).",
)
@click.option(
    "--suite",
    "suite_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Test suite YAML for error-injection tracking (repeatable).",
)
@click.option(
    "--threshold",
    default=0.0,
    type=float,
    show_default=True,
    help="Exit non-zero if overall_score is below this value (CI gating).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON on stdout instead of a table.",
)
def coverage_command(
    results_json: str | None,
    fixture_paths: tuple[str, ...],
    suite_paths: tuple[str, ...],
    threshold: float,
    json_output: bool,
) -> None:
    from mcptest.coverage import analyze_coverage

    console = Console(stderr=json_output)

    # --- Load fixtures ---
    if not fixture_paths:
        console.print("[red]error:[/red] at least one --fixture is required")
        sys.exit(1)

    fixtures = []
    for fp in fixture_paths:
        try:
            fixtures.append(load_fixture(fp))
        except FixtureLoadError as exc:
            console.print(f"[red]error:[/red] could not load fixture {fp}: {exc}")
            sys.exit(1)

    # --- Load test suites for inject_error tracking ---
    test_cases = []
    for sp in suite_paths:
        try:
            suite = load_test_suite(sp)
            test_cases.extend(suite.cases)
        except TestSuiteLoadError as exc:
            console.print(f"[red]error:[/red] could not load suite {sp}: {exc}")
            sys.exit(1)

    # --- Extract traces from results JSON ---
    traces = []
    if results_json:
        try:
            raw = Path(results_json).read_text(encoding="utf-8")
            data = json_module.loads(raw)
        except Exception as exc:
            console.print(f"[red]error:[/red] could not read {results_json}: {exc}")
            sys.exit(1)

        if isinstance(data, dict) and "cases" in data:
            # Full mcptest run output produced by `mcptest run --json`
            for case_data in data.get("cases", []):
                trace_data = case_data.get("trace", {})
                traces.append(Trace.from_dict(trace_data))
        else:
            # Assume single trace JSON (from `mcptest record` / `Trace.save()`)
            try:
                traces.append(Trace.from_dict(data))
            except Exception as exc:
                console.print(f"[red]error:[/red] could not parse trace: {exc}")
                sys.exit(1)

    report = analyze_coverage(fixtures, traces, test_cases=test_cases or None)

    if json_output:
        click.echo(json_module.dumps(report.to_dict(), indent=2, default=str))
    else:
        _render_coverage(console, report)

    if threshold > 0.0 and report.overall_score < threshold:
        if not json_output:
            console.print(
                f"[red]✗[/red] coverage {report.overall_score:.1%}"
                f" is below threshold {threshold:.1%}"
            )
        sys.exit(1)


def _render_coverage(console: Console, report: Any) -> None:
    """Render a CoverageReport to the Rich console."""
    from rich.table import Table as _Table

    # Tool / response table
    tool_table = _Table(title="Fixture Coverage", show_lines=False)
    tool_table.add_column("Tool")
    tool_table.add_column("Calls", justify="right")
    tool_table.add_column("Responses", justify="right")
    tool_table.add_column("Hit", justify="right")
    tool_table.add_column("Score", justify="right")

    for t in report.tool_details:
        score = t.responses_hit / t.responses_total if t.responses_total else 1.0
        if score >= 0.8:
            score_str = f"[green]{score:.0%}[/green]"
        elif score >= 0.5:
            score_str = f"[yellow]{score:.0%}[/yellow]"
        else:
            score_str = f"[red]{score:.0%}[/red]"

        call_indicator = "[green]✓[/green]" if t.call_count > 0 else "[red]✗[/red]"
        tool_table.add_row(
            t.name,
            f"{call_indicator} {t.call_count}",
            str(t.responses_total),
            str(t.responses_hit),
            score_str,
        )
    console.print(tool_table)

    # Error table
    if report.error_details:
        err_table = _Table(title="Error Coverage", show_lines=False)
        err_table.add_column("Error")
        err_table.add_column("Tool Scope")
        err_table.add_column("Injected", justify="center")
        err_table.add_column("Count", justify="right")
        for e in report.error_details:
            inj = "[green]✓[/green]" if e.injected else "[red]✗[/red]"
            err_table.add_row(
                e.name,
                e.tool or "[dim]any[/dim]",
                inj,
                str(e.injection_count),
            )
        console.print(err_table)

    # Overall score
    score = report.overall_score
    color = "green" if score >= 0.8 else ("yellow" if score >= 0.5 else "red")
    console.print(f"\nOverall coverage score: [{color}]{score:.1%}[/{color}]")

    # Suggestions
    if report.uncovered_summary:
        console.print("\n[bold]Suggestions:[/bold]")
        for s in report.uncovered_summary:
            console.print(f"  [dim]•[/dim] {s}")


# ---------------------------------------------------------------------------
# watch — file-watching test runner
# ---------------------------------------------------------------------------


@click.command(help="Watch test/fixture files and re-run affected tests on save.")
@click.argument(
    "path",
    default="tests",
    type=click.Path(exists=False, resolve_path=True),
)
@click.option(
    "--clear/--no-clear",
    default=True,
    help="Clear screen between runs (default: clear).",
)
@click.option(
    "-j",
    "--parallel",
    "parallel_workers",
    default=1,
    type=int,
    help="Run cases in parallel (0 = auto-detect CPU count, 1 = serial).",
)
@click.option("--fail-fast", is_flag=True, help="Stop at the first failing case.")
@click.option(
    "--debounce",
    default=300,
    type=int,
    help="Change-coalescing window in milliseconds (default: 300).",
)
@click.option(
    "--watch-extra",
    multiple=True,
    type=click.Path(resolve_path=True),
    help="Additional directories to watch (repeatable). Changes here trigger a full re-run.",
)
@click.option(
    "--retry",
    "retry_override",
    default=None,
    type=int,
    help="Override retry count for every case.",
)
@click.option(
    "--tolerance",
    "tolerance_override",
    default=None,
    type=float,
    help="Override pass-rate tolerance for every case (0.0–1.0).",
)
def watch_command(
    path: str,
    clear: bool,
    parallel_workers: int,
    fail_fast: bool,
    debounce: int,
    watch_extra: tuple[str, ...],
    retry_override: int | None,
    tolerance_override: float | None,
) -> None:
    from mcptest.watch.engine import WatchConfig, WatchEngine  # noqa: PLC0415

    config = WatchConfig(
        test_paths=[Path(path)],
        extra_watch=[Path(p) for p in watch_extra],
        clear_screen=clear,
        parallel_workers=parallel_workers,
        fail_fast=fail_fast,
        debounce_ms=debounce,
        retry_override=retry_override,
        tolerance_override=tolerance_override,
    )
    try:
        WatchEngine(config).run()
    except KeyboardInterrupt:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# scorecard — weighted quality report card with CI exit-code gating
# ---------------------------------------------------------------------------


@click.command(
    help=(
        "Render a weighted quality scorecard from a saved trace JSON file.\n\n"
        "Each registered metric is computed, compared against a per-metric\n"
        "threshold, and rolled up into a single weighted composite score.\n\n"
        "Exit code is 1 if the composite score is below the threshold (use\n"
        "--fail-under to override the threshold at the command line)."
    )
)
@click.argument("trace_json", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help=(
        "YAML file with thresholds, weights, and composite_threshold.  "
        "Keys: thresholds (dict), weights (dict), composite_threshold (float), "
        "default_threshold (float)."
    ),
)
@click.option(
    "--fail-under",
    "fail_under",
    default=None,
    type=float,
    help="Exit 1 if composite score is below this value (overrides --config).",
)
@click.option(
    "--fixture",
    "fixture_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Fixture YAML for schema_compliance and tool_coverage metrics (repeatable).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON on stdout instead of a table.",
)
def scorecard_command(
    trace_json: str,
    config_path: str | None,
    fail_under: float | None,
    fixture_paths: tuple[str, ...],
    json_output: bool,
) -> None:
    from mcptest.scorecard import Scorecard, ScorecardConfig, render_scorecard

    console = Console(stderr=json_output)

    try:
        trace = Trace.load(trace_json)
    except Exception as exc:
        console.print(f"[red]error:[/red] could not load trace: {exc}")
        sys.exit(1)

    # Load optional scorecard config YAML.
    config: ScorecardConfig | None = None
    if config_path is not None:
        try:
            raw = Path(config_path).read_text(encoding="utf-8")
            config_data = yaml.safe_load(raw) or {}
            config = ScorecardConfig.from_dict(config_data)
        except Exception as exc:
            console.print(f"[red]error:[/red] could not load scorecard config: {exc}")
            sys.exit(1)

    if config is None:
        config = ScorecardConfig()

    # --fail-under overrides the composite_threshold from config.
    if fail_under is not None:
        config.composite_threshold = fail_under

    fixtures = []
    for fp in fixture_paths:
        try:
            fixtures.append(load_fixture(fp))
        except FixtureLoadError as exc:
            console.print(f"[red]error:[/red] could not load fixture {fp}: {exc}")
            sys.exit(1)

    scorecard = Scorecard.from_trace(trace, config, fixtures=fixtures or None)

    if json_output:
        click.echo(scorecard.to_json())
    else:
        render_scorecard(console, scorecard)

    if not scorecard.composite_passed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# conformance — MCP server protocol conformance testing
# ---------------------------------------------------------------------------


@click.command(
    help=(
        "Test an MCP server for protocol conformance.\n\n"
        "Runs up to 19 checks across 5 sections (initialization, tool_listing,\n"
        "tool_calling, error_handling, resources) and reports MUST / SHOULD / MAY\n"
        "violations.  Exit code is 1 when any MUST check fails (or any SHOULD\n"
        "check fails with --fail-on-should).\n\n"
        "Use --fixture to run checks in-process against a fixture YAML (fast, no\n"
        "subprocess).  Omit --fixture to spawn SERVER_COMMAND as a stdio MCP\n"
        "server subprocess."
    )
)
@click.argument("server_command", required=False, default=None)
@click.option(
    "--fixture",
    "fixture_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Fixture YAML — run checks in-process (no subprocess).",
)
@click.option(
    "--section",
    "sections",
    multiple=True,
    help="Only run checks in this section (repeatable).",
)
@click.option(
    "--severity",
    "severity_filter",
    type=click.Choice(["must", "should", "may"], case_sensitive=False),
    default=None,
    help="Only run checks at or above this severity.",
)
@click.option(
    "--fail-on-should",
    is_flag=True,
    help="Exit 1 if any SHOULD check fails (default: only MUST failures exit 1).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit machine-readable JSON on stdout instead of a human-friendly table.",
)
def conformance_command(
    server_command: str | None,
    fixture_path: str | None,
    sections: tuple[str, ...],
    severity_filter: str | None,
    fail_on_should: bool,
    json_output: bool,
) -> None:
    import anyio

    from mcptest.conformance import (
        ConformanceRunner,
        InProcessServer,
        Severity,
        make_stdio_server,
        render_conformance_report,
    )

    console = Console(stderr=json_output)

    if fixture_path is None and server_command is None:
        console.print(
            "[red]error:[/red] provide either SERVER_COMMAND or --fixture"
        )
        sys.exit(1)

    # Build severity filter list
    severity_map = {
        "must": [Severity.MUST],
        "should": [Severity.MUST, Severity.SHOULD],
        "may": [Severity.MUST, Severity.SHOULD, Severity.MAY],
    }
    severities = severity_map.get(severity_filter or "may")

    async def _run() -> list:
        if fixture_path is not None:
            # In-process mode: load fixture, build MockMCPServer
            from mcptest.fixtures.loader import load_fixture as _load_fixture
            from mcptest.mock_server.server import MockMCPServer

            try:
                fixture = _load_fixture(fixture_path)
            except Exception as exc:
                console.print(f"[red]error:[/red] could not load fixture: {exc}")
                sys.exit(1)

            mock = MockMCPServer(fixture)
            server = InProcessServer(mock=mock, fixture=fixture)
        else:
            # Subprocess stdio mode
            try:
                server = make_stdio_server(server_command)  # type: ignore[arg-type]
                await server.connect()  # type: ignore[union-attr]
            except Exception as exc:
                console.print(
                    f"[red]error:[/red] could not connect to server: {exc}"
                )
                sys.exit(1)

        try:
            runner = ConformanceRunner(
                server=server,
                sections=list(sections) if sections else None,
                severities=severities,
            )
            return await runner.run()
        finally:
            await server.close()

    try:
        results = anyio.run(_run)
    except SystemExit:
        raise

    if json_output:
        click.echo(render_conformance_report(results, as_json=True))
    else:
        render_conformance_report(results, as_json=False, console=Console())

    # Determine exit code
    must_failures = [
        r for r in results if not r.passed and not r.skipped and r.check.severity == Severity.MUST
    ]
    should_failures = [
        r for r in results
        if not r.passed and not r.skipped and r.check.severity == Severity.SHOULD
    ]

    if must_failures or (fail_on_should and should_failures):
        sys.exit(1)


# ---------------------------------------------------------------------------
# capture — live server discovery → auto-generated fixtures & tests
# ---------------------------------------------------------------------------


@click.command(
    help=(
        "Connect to a live MCP server and auto-generate fixture and test files.\n\n"
        "SERVER_COMMAND is the shell command to start the server, e.g. "
        "'python my_server.py'. mcptest will connect, enumerate all tools, "
        "sample responses with diverse arguments, and write a ready-to-use "
        "fixture YAML (and optionally a test-spec YAML) to the output directory.\n\n"
        "Examples:\n\n"
        "  mcptest capture 'python my_server.py'\n\n"
        "  mcptest capture 'npx my-mcp-server' --output fixtures/ --generate-tests\n\n"
        "  mcptest capture 'python server.py' --dry-run  # preview without writing"
    )
)
@click.argument("server_command")
@click.option(
    "--output",
    "-o",
    "output_dir",
    default=".",
    show_default=True,
    type=click.Path(file_okay=False),
    help="Directory where generated files are written.",
)
@click.option(
    "--generate-tests",
    is_flag=True,
    help="Also generate a test-spec YAML file alongside the fixture.",
)
@click.option(
    "--samples-per-tool",
    "samples_per_tool",
    type=int,
    default=3,
    show_default=True,
    help="Number of argument variations to try per tool.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Discover and sample without writing any files.",
)
@click.option(
    "--agent",
    "agent_cmd",
    default="python agent.py",
    show_default=True,
    help="Agent command embedded in generated test suites.",
)
def capture_command(
    server_command: str,
    output_dir: str,
    generate_tests: bool,
    samples_per_tool: int,
    dry_run: bool,
    agent_cmd: str,
) -> None:
    import anyio

    from mcptest.capture.runner import capture_server

    console = Console()

    async def _run() -> None:
        if dry_run:
            console.print("[bold]Dry run — no files will be written.[/bold]")

        console.print(f"[dim]Connecting to:[/dim] {server_command}")

        try:
            result = await capture_server(
                server_command,
                output_dir=output_dir,
                generate_tests=generate_tests,
                samples_per_tool=samples_per_tool,
                dry_run=dry_run,
                agent_cmd=agent_cmd,
            )
        except Exception as exc:
            console.print(f"[red]error:[/red] {exc}")
            sys.exit(1)

        disc = result.discovery
        console.print(
            f"[green]✓[/green] Connected to [bold]{disc.server_name}[/bold] "
            f"v{disc.server_version}"
        )
        console.print(
            f"[green]✓[/green] Discovered [bold]{result.tool_count}[/bold] tool(s)"
        )
        console.print(
            f"[green]✓[/green] Executed [bold]{result.sample_count}[/bold] sample call(s)"
        )

        if dry_run:
            console.print("\n[bold]Would generate:[/bold]")
            console.print(f"  fixture: [bold]{disc.server_name or 'captured'}.yaml[/bold]")
            if generate_tests:
                console.print(
                    f"  tests:   [bold]{disc.server_name or 'captured'}-tests.yaml[/bold]"
                )
            return

        if result.fixture_path:
            console.print(
                f"[green]✓[/green] Wrote fixture → [bold]{result.fixture_path}[/bold]"
            )
        for tp in result.test_paths:
            console.print(
                f"[green]✓[/green] Wrote tests   → [bold]{tp}[/bold]"
            )

        console.print("\n[bold]Next steps:[/bold]")
        if result.fixture_path:
            console.print(
                f"  mcptest run --fixture {result.fixture_path} <your-tests.yaml>"
            )
        console.print("  mcptest validate  # check generated files are valid")

    anyio.run(_run)
