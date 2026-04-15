"""Smart file-watching test runner engine."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from mcptest.testspec import TestSuiteLoadError
from mcptest.testspec.loader import discover_test_files, load_test_suite


@dataclass
class WatchConfig:
    """Configuration for the watch engine."""

    test_paths: list[Path] = field(default_factory=lambda: [Path("tests")])
    extra_watch: list[Path] = field(default_factory=list)
    clear_screen: bool = True
    parallel_workers: int = 1
    fail_fast: bool = False
    debounce_ms: int = 300
    retry_override: int | None = None
    tolerance_override: float | None = None


@dataclass
class ChangedSuites:
    """Result of resolving which test suites need re-running after file changes."""

    files: set[Path]
    affected_suites: list[Path]
    reason: str


class DependencyMap:
    """Reverse dependency index from fixture paths to the test suites that reference them."""

    def __init__(self) -> None:
        self._fixture_to_suites: dict[Path, list[Path]] = {}
        self._suite_paths: list[Path] = []
        self._extra_watch_dirs: list[Path] = []

    @classmethod
    def build(
        cls,
        test_root: Path,
        extra_watch: list[Path] | None = None,
    ) -> DependencyMap:
        """Parse all test YAML files under *test_root* and build the reverse index."""
        dm = cls()
        dm._extra_watch_dirs = list(extra_watch or [])
        dm._scan(test_root)
        return dm

    def _scan(self, test_root: Path) -> None:
        """Scan *test_root* and rebuild *_suite_paths* and *_fixture_to_suites*."""
        self._fixture_to_suites = {}
        self._suite_paths = []

        for suite_path in discover_test_files(test_root):
            self._suite_paths.append(suite_path)
            try:
                suite = load_test_suite(suite_path)
            except TestSuiteLoadError:
                continue

            base_dir = suite_path.parent
            for fixture_ref in suite.fixtures:
                p = Path(fixture_ref)
                if not p.is_absolute():
                    p = (base_dir / p).resolve()
                self._fixture_to_suites.setdefault(p, []).append(suite_path)

    def refresh(self, test_root: Path) -> None:
        """Re-scan *test_root* (call when test files themselves change)."""
        self._scan(test_root)

    @property
    def suite_paths(self) -> list[Path]:
        return list(self._suite_paths)

    @property
    def fixture_dirs(self) -> set[Path]:
        """Unique parent directories of all referenced fixture files."""
        return {p.parent for p in self._fixture_to_suites}

    def resolve_changes(self, changed_files: set[Path]) -> ChangedSuites:
        """Given a set of changed paths, return which test suites are affected.

        Resolution rules (in priority order):
        1. Anything inside an *extra_watch* directory → full re-run.
        2. A known fixture file → only suites referencing that fixture.
        3. A test suite file → just that suite.
        4. An unknown YAML inside a known fixture directory → full re-run (new fixture).
        5. Anything else → no suites affected.
        """
        # Rule 1: extra_watch directories trigger a full re-run.
        for changed in changed_files:
            for extra_dir in self._extra_watch_dirs:
                try:
                    changed.relative_to(extra_dir)
                    return ChangedSuites(
                        files=changed_files,
                        affected_suites=list(self._suite_paths),
                        reason=(
                            f"source change in {extra_dir.name}/ "
                            f"→ all {len(self._suite_paths)} suite(s)"
                        ),
                    )
                except ValueError:
                    pass

        affected: list[Path] = []
        seen: set[Path] = set()
        fixture_names: list[str] = []
        suite_path_set = {p.resolve() for p in self._suite_paths}

        for changed in changed_files:
            if changed.suffix.lower() not in (".yaml", ".yml"):
                continue
            resolved = changed.resolve()

            # Rule 2: known fixture.
            if resolved in self._fixture_to_suites:
                for suite_path in self._fixture_to_suites[resolved]:
                    if suite_path not in seen:
                        seen.add(suite_path)
                        affected.append(suite_path)
                fixture_names.append(changed.name)
                continue

            # Rule 3: test suite file changed.
            if resolved in suite_path_set:
                if resolved not in seen:
                    seen.add(resolved)
                    affected.append(resolved)
                continue

            # Rule 4: unknown YAML inside a known fixture directory → full re-run.
            for fixture_dir in self.fixture_dirs:
                try:
                    changed.relative_to(fixture_dir)
                    return ChangedSuites(
                        files=changed_files,
                        affected_suites=list(self._suite_paths),
                        reason=(
                            f"new fixture {changed.name} "
                            f"→ all {len(self._suite_paths)} suite(s)"
                        ),
                    )
                except ValueError:
                    pass

        if not affected:
            return ChangedSuites(
                files=changed_files,
                affected_suites=[],
                reason="no affected suites",
            )

        if fixture_names:
            reason = f"fixture {', '.join(fixture_names)} → {len(affected)} suite(s)"
        else:
            reason = f"test change → {len(affected)} suite(s)"

        return ChangedSuites(files=changed_files, affected_suites=affected, reason=reason)


class WatchEngine:
    """Watch test and fixture files, re-running affected tests on every save."""

    def __init__(self, config: WatchConfig) -> None:
        self.config = config
        self.console = Console()

    def run(self) -> None:
        """Main blocking watch loop.  Exits cleanly on :exc:`KeyboardInterrupt`."""
        try:
            import watchfiles
        except ImportError:  # pragma: no cover
            self.console.print(
                "[red]error:[/red] watchfiles is required for watch mode.\n"
                "Install it with: [bold]pip install watchfiles[/bold]"
            )
            sys.exit(1)

        # Lazy imports to avoid circular-import at module load time.
        from mcptest.cli.commands import _render_results, execute_test_files  # noqa: PLC0415

        test_path = self.config.test_paths[0]
        dep_map = DependencyMap.build(test_path, extra_watch=self.config.extra_watch)

        if not dep_map.suite_paths:
            self.console.print(
                f"[yellow]no test files found under[/yellow] {test_path}"
            )
            return

        # ── initial full run ─────────────────────────────────────────────────
        n = len(dep_map.suite_paths)
        self.console.print(
            f"[bold cyan]mcptest watch[/bold cyan] — running {n} suite(s)..."
        )
        results = execute_test_files(
            dep_map.suite_paths,
            parallel_workers=self.config.parallel_workers,
            fail_fast=self.config.fail_fast,
            retry_override=self.config.retry_override,
            tolerance_override=self.config.tolerance_override,
        )
        _render_results(self.console, results)

        # ── watch loop ───────────────────────────────────────────────────────
        watch_dirs = self._collect_watch_paths(dep_map)
        self.console.print(
            f"\n[dim]Watching {len(watch_dirs)} path(s) for changes… "
            "(press [bold]Ctrl+C[/bold] to exit)[/dim]"
        )

        try:
            for changes in watchfiles.watch(
                *watch_dirs,
                debounce=self.config.debounce_ms,
                raise_interrupt=True,
            ):
                changed_paths = {Path(c[1]) for c in changes}
                changed_suite = dep_map.resolve_changes(changed_paths)

                # Refresh the dep map if any test suite files changed.
                suite_resolved = {p.resolve() for p in dep_map.suite_paths}
                if any(p.resolve() in suite_resolved for p in changed_paths):
                    dep_map.refresh(test_path)

                if not changed_suite.affected_suites:
                    continue

                if self.config.clear_screen:
                    self.console.clear()

                self.console.print(f"[dim]Changed:[/dim] {changed_suite.reason}")
                self.console.print(
                    f"[bold cyan]Re-running "
                    f"{len(changed_suite.affected_suites)} suite(s)…[/bold cyan]"
                )

                results = execute_test_files(
                    changed_suite.affected_suites,
                    parallel_workers=self.config.parallel_workers,
                    fail_fast=self.config.fail_fast,
                    retry_override=self.config.retry_override,
                    tolerance_override=self.config.tolerance_override,
                )
                _render_results(self.console, results)

                # Recompute watch dirs after potential dep-map refresh.
                watch_dirs = self._collect_watch_paths(dep_map)
                self.console.print(
                    "\n[dim]Watching for changes… (Ctrl+C to exit)[/dim]"
                )

        except KeyboardInterrupt:
            self.console.print("\n[dim]Watch stopped.[/dim]")

    def _collect_watch_paths(self, dep_map: DependencyMap) -> set[Path]:
        """Return the union of test dirs, fixture dirs, and extra_watch paths."""
        paths: set[Path] = set()
        for test_path in self.config.test_paths:
            if test_path.exists():
                paths.add(test_path)
        for fixture_dir in dep_map.fixture_dirs:
            if fixture_dir.exists():
                paths.add(fixture_dir)
        for extra in self.config.extra_watch:
            if extra.exists():
                paths.add(extra)
        # Fallback: watch the test path even if it doesn't exist yet.
        if not paths:
            paths.update(self.config.test_paths)
        return paths
