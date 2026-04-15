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
from mcptest.fixtures.loader import FixtureLoadError, load_fixture
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
