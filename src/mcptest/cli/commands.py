"""Concrete click subcommands for the mcptest CLI."""

from __future__ import annotations

import json as json_module
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from mcptest.runner import Runner, RunnerError, SubprocessAdapter, Trace
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

    @property
    def passed(self) -> bool:
        return (
            self.error is None
            and self.trace.succeeded
            and all(r.passed for r in self.assertion_results)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite_name,
            "case": self.case_name,
            "passed": self.passed,
            "error": self.error,
            "trace": self.trace.to_dict(),
            "assertions": [r.to_dict() for r in self.assertion_results],
        }


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
    help="Emit machine-readable JSON on stdout instead of a human-friendly table.",
)
@click.option(
    "--fail-fast",
    is_flag=True,
    help="Stop at the first failing case.",
)
def run_command(path: str, ci: bool, json_output: bool, fail_fast: bool) -> None:
    console = Console(stderr=json_output)

    files = discover_test_files(path)
    if not files:
        console.print(f"[yellow]no test files found under[/yellow] {path}")
        return

    all_results: list[CaseResult] = []
    stop = False
    for test_file in files:
        if stop:
            break
        try:
            suite = load_test_suite(test_file)
        except TestSuiteLoadError as exc:
            console.print(f"[red]× {test_file}[/red] {exc}")
            all_results.append(
                CaseResult(
                    suite_name=str(test_file),
                    case_name="<load>",
                    trace=Trace(),
                    assertion_results=[],
                    error=str(exc),
                )
            )
            if fail_fast:
                break
            continue

        for case_result in _iter_suite_results(suite, test_file):
            all_results.append(case_result)
            if fail_fast and not case_result.passed:
                stop = True
                break

    if json_output:
        payload = {
            "passed": sum(1 for r in all_results if r.passed),
            "failed": sum(1 for r in all_results if not r.passed),
            "total": len(all_results),
            "cases": [r.to_dict() for r in all_results],
        }
        click.echo(json_module.dumps(payload, indent=2, default=str))
    else:
        _render_results(console, all_results)

    failed = sum(1 for r in all_results if not r.passed)
    if failed and ci:
        sys.exit(1)


def _iter_suite_results(suite: TestSuite, source: Path):
    """Yield one CaseResult per case in order, lazily.

    Yielding instead of building a list lets the top-level runner honour
    ``--fail-fast`` without running every remaining case first.
    """
    base_dir = source.parent
    fixture_paths = suite.resolve_fixtures(base_dir)

    try:
        adapter = suite.agent.build_adapter(base_dir)
        runner = Runner(fixtures=fixture_paths, agent=adapter)
    except (RunnerError, FixtureLoadError, ValueError) as exc:
        yield CaseResult(
            suite_name=suite.name,
            case_name="<setup>",
            trace=Trace(),
            assertion_results=[],
            error=str(exc),
        )
        return

    for case in suite.cases:
        yield _run_case(runner, suite, case)


def _run_case(runner: Runner, suite: TestSuite, case: TestCase) -> CaseResult:
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

    try:
        assertions = parse_assertions(case.assertions)
    except ValueError as exc:
        return CaseResult(
            suite_name=suite.name,
            case_name=case.name,
            trace=trace,
            assertion_results=[],
            error=f"assertion parse error: {exc}",
        )

    results = check_all(assertions, trace)
    return CaseResult(
        suite_name=suite.name,
        case_name=case.name,
        trace=trace,
        assertion_results=results,
    )


def _render_results(console: Console, results: list[CaseResult]) -> None:
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
