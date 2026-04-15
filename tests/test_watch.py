"""Tests for mcptest.watch — DependencyMap, ChangedSuites, WatchEngine, watch_command."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from mcptest.cli.main import main
from mcptest.watch.engine import ChangedSuites, DependencyMap, WatchConfig, WatchEngine


# ---------------------------------------------------------------------------
# Helpers — project layout in a tmp dir
# ---------------------------------------------------------------------------

_FIXTURE_YAML = """\
server: { name: mock-example }
tools:
  - name: greet
    responses:
      - return: { ok: true }
"""

_FIXTURE_B_YAML = """\
server: { name: mock-other }
tools:
  - name: ping
    responses:
      - return: { pong: true }
"""

_PASSING_AGENT = """\
import json, os, sys, time
trace = os.environ['MCPTEST_TRACE_FILE']
sys.stdin.read()
with open(trace, 'a') as f:
    f.write(json.dumps({
        'index': 0, 'tool': 'greet', 'server': 'mock-example',
        'arguments': {'name': 'world'}, 'result': {'ok': True},
        'error': None, 'error_code': None,
        'latency_ms': 1.0, 'timestamp': time.time(),
    }) + '\\n')
print('ok')
"""


def _make_suite_yaml(fixture_rel: str) -> str:
    return (
        "name: example suite\n"
        "fixtures:\n"
        f"  - {fixture_rel}\n"
        "agent:\n"
        f"  command: {sys.executable} ../examples/agent.py\n"
        "cases:\n"
        "  - name: greet world\n"
        "    input: hello\n"
        "    assertions:\n"
        "      - tool_called: greet\n"
    )


def _write_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write a minimal project and return (fixture_file, suite_file, tests_dir)."""
    (tmp_path / "fixtures").mkdir()
    fixture_file = tmp_path / "fixtures" / "example.yaml"
    fixture_file.write_text(_FIXTURE_YAML)

    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "agent.py").write_text(_PASSING_AGENT)

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    suite_file = tests_dir / "test_example.yaml"
    suite_file.write_text(_make_suite_yaml("../fixtures/example.yaml"))

    return fixture_file, suite_file, tests_dir


# ---------------------------------------------------------------------------
# DependencyMap.build
# ---------------------------------------------------------------------------


class TestDependencyMapBuild:
    def test_discovers_suite_and_fixture(self, tmp_path: Path) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)

        dm = DependencyMap.build(tests_dir)

        assert suite_file in dm.suite_paths
        assert fixture_file.resolve() in dm._fixture_to_suites
        assert suite_file in dm._fixture_to_suites[fixture_file.resolve()]

    def test_empty_test_dir(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        dm = DependencyMap.build(tests_dir)

        assert dm.suite_paths == []
        assert dm._fixture_to_suites == {}

    def test_nonexistent_test_dir(self, tmp_path: Path) -> None:
        dm = DependencyMap.build(tmp_path / "tests")

        assert dm.suite_paths == []

    def test_suite_with_invalid_yaml_is_skipped(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_bad.yaml").write_text("[unclosed\n")

        dm = DependencyMap.build(tests_dir)

        # The suite path is recorded but no fixture mappings are built for it.
        assert len(dm.suite_paths) == 1
        assert dm._fixture_to_suites == {}

    def test_fixture_dirs_property(self, tmp_path: Path) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)

        dm = DependencyMap.build(tests_dir)

        fixture_dir = fixture_file.parent
        assert fixture_dir in dm.fixture_dirs

    def test_multiple_suites_referencing_same_fixture(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "fixtures").mkdir()
        fixture = tmp_path / "fixtures" / "shared.yaml"
        fixture.write_text(_FIXTURE_YAML)

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        for i in range(3):
            (tests_dir / f"test_suite{i}.yaml").write_text(
                _make_suite_yaml("../fixtures/shared.yaml")
            )

        dm = DependencyMap.build(tests_dir)

        assert len(dm.suite_paths) == 3
        assert len(dm._fixture_to_suites[fixture.resolve()]) == 3


# ---------------------------------------------------------------------------
# DependencyMap.resolve_changes
# ---------------------------------------------------------------------------


class TestDependencyMapResolveChanges:
    def test_fixture_change_returns_referencing_suites(
        self, tmp_path: Path
    ) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        dm = DependencyMap.build(tests_dir)

        result = dm.resolve_changes({fixture_file})

        assert suite_file in result.affected_suites
        assert "fixture" in result.reason

    def test_test_file_change_returns_that_suite(self, tmp_path: Path) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        dm = DependencyMap.build(tests_dir)

        result = dm.resolve_changes({suite_file})

        assert result.affected_suites == [suite_file.resolve()]
        assert "test change" in result.reason

    def test_extra_watch_change_triggers_full_rerun(
        self, tmp_path: Path
    ) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        changed_src = src_dir / "agent.py"
        changed_src.write_text("# changed")

        dm = DependencyMap.build(tests_dir, extra_watch=[src_dir])

        result = dm.resolve_changes({changed_src})

        assert result.affected_suites == dm.suite_paths
        assert "source change" in result.reason

    def test_unrelated_change_returns_empty(self, tmp_path: Path) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        unrelated = tmp_path / "README.md"
        unrelated.write_text("# docs")
        dm = DependencyMap.build(tests_dir)

        result = dm.resolve_changes({unrelated})

        assert result.affected_suites == []
        assert result.reason == "no affected suites"

    def test_unknown_yaml_in_fixture_dir_triggers_full_rerun(
        self, tmp_path: Path
    ) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        dm = DependencyMap.build(tests_dir)
        new_fixture = tmp_path / "fixtures" / "new_tool.yaml"
        # Don't write the file — the change event just names the path.

        result = dm.resolve_changes({new_fixture})

        assert result.affected_suites == dm.suite_paths
        assert "new fixture" in result.reason

    def test_non_yaml_change_returns_empty(self, tmp_path: Path) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        dm = DependencyMap.build(tests_dir)
        changed_py = tmp_path / "agent.py"

        result = dm.resolve_changes({changed_py})

        assert result.affected_suites == []


# ---------------------------------------------------------------------------
# DependencyMap.refresh
# ---------------------------------------------------------------------------


class TestDependencyMapRefresh:
    def test_refresh_picks_up_new_suite(self, tmp_path: Path) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        dm = DependencyMap.build(tests_dir)

        assert len(dm.suite_paths) == 1

        # Add a new suite file, then refresh.
        new_suite = tests_dir / "test_new.yaml"
        new_suite.write_text(_make_suite_yaml("../fixtures/example.yaml"))
        dm.refresh(tests_dir)

        assert len(dm.suite_paths) == 2
        assert new_suite in dm.suite_paths


# ---------------------------------------------------------------------------
# ChangedSuites reason strings
# ---------------------------------------------------------------------------


class TestChangedSuitesReason:
    def test_fixture_reason_includes_name(self, tmp_path: Path) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        dm = DependencyMap.build(tests_dir)

        result = dm.resolve_changes({fixture_file})

        assert "example.yaml" in result.reason
        assert "suite" in result.reason

    def test_test_change_reason(self, tmp_path: Path) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        dm = DependencyMap.build(tests_dir)

        result = dm.resolve_changes({suite_file})

        assert "test change" in result.reason


# ---------------------------------------------------------------------------
# execute_test_files shared helper
# ---------------------------------------------------------------------------


class TestExecuteTestFiles:
    def test_runs_all_files_and_returns_results(self, tmp_path: Path) -> None:
        from mcptest.cli.commands import execute_test_files

        _write_project(tmp_path)
        tests_dir = tmp_path / "tests"
        suite_files = [tests_dir / "test_example.yaml"]

        results = execute_test_files(suite_files)

        assert len(results) == 1
        assert results[0].passed

    def test_on_result_callback_is_called(self, tmp_path: Path) -> None:
        from mcptest.cli.commands import execute_test_files

        _write_project(tmp_path)
        tests_dir = tmp_path / "tests"
        suite_files = [tests_dir / "test_example.yaml"]
        seen = []

        execute_test_files(suite_files, on_result=seen.append)

        assert len(seen) == 1

    def test_fail_fast_stops_after_first_failure(self, tmp_path: Path) -> None:
        from mcptest.cli.commands import execute_test_files

        _write_project(tmp_path)
        tests_dir = tmp_path / "tests"
        # Write two suite files; the first will fail due to missing fixture.
        (tests_dir / "test_bad.yaml").write_text(
            "name: bad\n"
            "fixtures:\n"
            "  - ../fixtures/does_not_exist.yaml\n"
            f"agent:\n  command: {sys.executable} echo.py\n"
            "cases:\n  - name: c\n    input: x\n"
        )
        suite_files = [
            tests_dir / "test_bad.yaml",
            tests_dir / "test_example.yaml",
        ]

        results = execute_test_files(suite_files, fail_fast=True)

        # Should stop after the first (failing) suite.
        assert len(results) == 1
        assert not results[0].passed

    def test_load_error_returns_error_case_result(self, tmp_path: Path) -> None:
        from mcptest.cli.commands import execute_test_files

        _write_project(tmp_path)
        bad_file = tmp_path / "tests" / "test_bad.yaml"
        bad_file.write_text("[invalid yaml[\n")

        results = execute_test_files([bad_file])

        assert len(results) == 1
        assert results[0].error is not None
        assert not results[0].passed


# ---------------------------------------------------------------------------
# WatchEngine — initial run and watch-path collection
# ---------------------------------------------------------------------------


class TestWatchEngineInitialRun:
    def test_initial_run_executes_all_suites(self, tmp_path: Path) -> None:
        """WatchEngine runs all discovered suites before entering the watch loop."""
        from watchfiles import Change

        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        (tmp_path / "examples" / "agent.py").write_text(_PASSING_AGENT)

        config = WatchConfig(
            test_paths=[tests_dir],
            clear_screen=False,
        )
        engine = WatchEngine(config)
        executed: list[Path] = []

        original_execute = None

        def fake_execute(files, **kwargs):
            executed.extend(files)
            # Return a dummy passing result so _render_results has something.
            from mcptest.runner.trace import Trace
            from mcptest.cli.commands import CaseResult

            return [
                CaseResult(
                    suite_name="example suite",
                    case_name="greet world",
                    trace=Trace(),
                    assertion_results=[],
                )
            ]

        # Patch watchfiles.watch to yield one empty batch then stop.
        with (
            patch("mcptest.watch.engine.WatchEngine.run", wraps=engine.run),
            patch(
                "watchfiles.watch",
                return_value=iter([]),  # no changes — loop exits immediately
            ),
            patch(
                "mcptest.cli.commands.execute_test_files",
                side_effect=fake_execute,
            ),
        ):
            engine.run()

        assert suite_file in executed

    def test_no_test_files_exits_early(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        config = WatchConfig(test_paths=[tests_dir], clear_screen=False)
        engine = WatchEngine(config)

        # Should return without calling watchfiles.watch at all.
        with patch("watchfiles.watch") as mock_watch:
            engine.run()

        mock_watch.assert_not_called()

    def test_watch_paths_include_fixture_dirs(self, tmp_path: Path) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        config = WatchConfig(test_paths=[tests_dir], clear_screen=False)
        engine = WatchEngine(config)
        dep_map = DependencyMap.build(tests_dir)

        watch_paths = engine._collect_watch_paths(dep_map)

        assert tests_dir in watch_paths
        assert fixture_file.parent in watch_paths

    def test_watch_paths_include_extra_watch(self, tmp_path: Path) -> None:
        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        config = WatchConfig(
            test_paths=[tests_dir],
            extra_watch=[src_dir],
            clear_screen=False,
        )
        engine = WatchEngine(config)
        dep_map = DependencyMap.build(tests_dir, extra_watch=[src_dir])

        watch_paths = engine._collect_watch_paths(dep_map)

        assert src_dir in watch_paths


# ---------------------------------------------------------------------------
# WatchEngine — change-triggered re-run
# ---------------------------------------------------------------------------


class TestWatchEngineChangeHandling:
    def test_fixture_change_reruns_affected_suites(self, tmp_path: Path) -> None:
        """When a fixture changes, only the suites referencing it are re-run."""
        from watchfiles import Change

        fixture_file, suite_file, tests_dir = _write_project(tmp_path)
        config = WatchConfig(test_paths=[tests_dir], clear_screen=False)
        engine = WatchEngine(config)

        rerun_files: list[list[Path]] = []

        def fake_execute(files, **kwargs):
            rerun_files.append(list(files))
            from mcptest.runner.trace import Trace
            from mcptest.cli.commands import CaseResult

            return [
                CaseResult(
                    suite_name="s",
                    case_name="c",
                    trace=Trace(),
                    assertion_results=[],
                )
            ]

        fixture_change = {(Change.modified, str(fixture_file))}

        with (
            patch("watchfiles.watch", return_value=iter([fixture_change])),
            patch(
                "mcptest.cli.commands.execute_test_files",
                side_effect=fake_execute,
            ),
        ):
            engine.run()

        # First call: initial full run; second call: fixture-triggered re-run.
        assert len(rerun_files) == 2
        # The second run contains the affected suite.
        assert suite_file in rerun_files[1]


# ---------------------------------------------------------------------------
# CLI: watch --help
# ---------------------------------------------------------------------------


class TestWatchCommandHelp:
    def test_help_renders(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["watch", "--help"])
        assert result.exit_code == 0
        assert "watch" in result.output.lower()
        assert "--clear" in result.output
        assert "--debounce" in result.output
        assert "--watch-extra" in result.output

    def test_watch_listed_in_main_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "watch" in result.output
