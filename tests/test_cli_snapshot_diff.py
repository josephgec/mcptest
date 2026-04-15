"""CLI tests for `mcptest snapshot` and `mcptest diff`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from mcptest.cli.main import main
from mcptest.diff import BaselineStore


_FIXTURE = """\
server: { name: mock-e2e }
tools:
  - name: greet
    responses:
      - return: { ok: true }
"""

_AGENT_TEMPLATE = """\
import json, os, sys, time
trace = os.environ['MCPTEST_TRACE_FILE']
inp = sys.stdin.read().strip()
record = {{
    'tool': 'greet', 'server': 'mock-e2e',
    'arguments': {{'name': inp or 'world'}},
    'result': {{'ok': True, 'echo': inp}},
    'error': None, 'error_code': None,
    'latency_ms': 1.0, 'timestamp': time.time(),
}}
with open(trace, 'a') as f:
    f.write(json.dumps(record) + '\\n')
print('agent output:', inp)
"""


def _write_project(tmp_path: Path, agent_src: str | None = None) -> Path:
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "f.yaml").write_text(_FIXTURE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "agent.py").write_text(agent_src or _AGENT_TEMPLATE.format())
    (tmp_path / "tests" / "test_snap.yaml").write_text(
        "name: snap-suite\n"
        "fixtures:\n  - ../fixtures/f.yaml\n"
        "agent:\n"
        f"  command: {sys.executable} ../agent.py\n"
        "  timeout_s: 10\n"
        "cases:\n"
        "  - name: hello\n"
        "    input: world\n"
    )
    return tmp_path


class TestSnapshotCommand:
    def test_saves_baselines(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "snapshot",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "base"),
            ],
        )
        assert result.exit_code == 0
        assert "saved baseline" in result.output
        store = BaselineStore(tmp_path / "base")
        assert "snap_suite__hello" in store.list_ids()

    def test_skips_existing_without_update(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        runner.invoke(
            main,
            [
                "snapshot",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "base"),
            ],
        )
        result = runner.invoke(
            main,
            [
                "snapshot",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "base"),
            ],
        )
        assert result.exit_code == 0
        assert "skipped" in result.output.lower()

    def test_update_overwrites(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        runner.invoke(
            main,
            [
                "snapshot",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "base"),
            ],
        )
        result = runner.invoke(
            main,
            [
                "snapshot",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "base"),
                "--update",
            ],
        )
        assert result.exit_code == 0
        assert "saved baseline" in result.output

    def test_no_files(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "snapshot",
                str(tmp_path / "empty"),
                "--baseline-dir",
                str(tmp_path / "base"),
            ],
        )
        assert result.exit_code == 0
        assert "no test files" in result.output


class TestDiffCommand:
    def test_no_regressions(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        runner.invoke(
            main,
            [
                "snapshot",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "base"),
            ],
        )
        result = runner.invoke(
            main,
            [
                "diff",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "base"),
                "--ci",
            ],
        )
        assert result.exit_code == 0
        assert "no regressions" in result.output

    def test_regression_detected(self, tmp_path: Path) -> None:
        # Write the baseline with the original agent, then mutate the agent
        # so the output and result differ on the next run.
        _write_project(tmp_path)
        runner = CliRunner()
        runner.invoke(
            main,
            [
                "snapshot",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "base"),
            ],
        )
        (tmp_path / "agent.py").write_text(
            "import json, os, sys, time\n"
            "trace = os.environ['MCPTEST_TRACE_FILE']\n"
            "inp = sys.stdin.read().strip()\n"
            "with open(trace, 'a') as f:\n"
            "    f.write(json.dumps({\n"
            "        'tool': 'greet', 'server': 'mock-e2e',\n"
            "        'arguments': {'name': 'CHANGED'},\n"
            "        'result': {'ok': False}, 'error': None,\n"
            "        'error_code': None, 'latency_ms': 1.0,\n"
            "        'timestamp': time.time(),\n"
            "    }) + '\\n')\n"
            "print('DIFFERENT OUTPUT')\n"
        )

        result = runner.invoke(
            main,
            [
                "diff",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "base"),
                "--ci",
            ],
        )
        assert result.exit_code == 1
        assert "regression" in result.output

    def test_missing_baseline(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "diff",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "never"),
                "--ci",
            ],
        )
        assert result.exit_code == 1
        assert "no baseline" in result.output

    def test_missing_baseline_without_ci_is_tolerant(self, tmp_path: Path) -> None:
        _write_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "diff",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "never"),
            ],
        )
        assert result.exit_code == 0

    def test_no_test_files(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "diff",
                str(tmp_path / "empty"),
                "--baseline-dir",
                str(tmp_path / "base"),
            ],
        )
        assert result.exit_code == 0
        assert "no test files" in result.output

    def test_broken_fixture_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_bad.yaml").write_text("[unclosed\n")
        (tmp_path / "tests" / "test_good.yaml").write_text(
            "name: good\n"
            "fixtures:\n  - ../missing.yaml\n"
            "agent:\n"
            "  command: /bin/true\n"
            "cases:\n"
            "  - name: c\n"
            "    input: x\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "diff",
                str(tmp_path / "tests"),
                "--baseline-dir",
                str(tmp_path / "base"),
            ],
        )
        # Everything got skipped, but the command still completes.
        assert result.exit_code == 0
