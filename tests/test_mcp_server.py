"""Round-trip tests for the mcptest MCP server.

Every test goes through the real MCP protocol via
``mcp.shared.memory.create_connected_server_and_client_session`` — no
short-circuit calls to the adapter functions.  This ensures that MCP-level
serialisation, tool dispatch, and error reporting all work end-to-end.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from mcptest.mcp_server.server import build_server

QUICKSTART = Path(__file__).parent.parent / "examples" / "quickstart"
EXAMPLE_FIXTURE = Path(__file__).parent.parent / "examples" / "quickstart" / "fixtures" / "hello.yaml"


# ---------------------------------------------------------------------------
# Helper: connected in-process session
# ---------------------------------------------------------------------------


async def _session_ctx(name: str = "mcptest"):
    """Async context manager that yields a connected MCP ClientSession."""
    from mcp.shared.memory import create_connected_server_and_client_session

    srv = build_server(name)
    return create_connected_server_and_client_session(srv, raise_exceptions=True)


def _parse(result: Any) -> Any:
    """Parse JSON from the first text content block of a call result."""
    return json.loads(result.content[0].text)


# ---------------------------------------------------------------------------
# Protocol-level: list_tools
# ---------------------------------------------------------------------------


class TestListTools:
    async def test_returns_ten_tools(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.list_tools()
        tool_names = {t.name for t in result.tools}
        expected = {
            "run_tests", "install_pack", "list_packs", "snapshot",
            "diff_baselines", "explain", "capture", "conformance",
            "validate", "coverage",
        }
        assert tool_names == expected

    async def test_every_tool_has_description_and_schema(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.list_tools()

        for tool in result.tools:
            assert tool.description, f"{tool.name} has no description"
            assert tool.inputSchema, f"{tool.name} has no inputSchema"
            assert tool.inputSchema.get("type") == "object"


# ---------------------------------------------------------------------------
# list_packs
# ---------------------------------------------------------------------------


class TestListPacks:
    async def test_returns_all_six(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("list_packs", {})

        payload = _parse(result)
        assert "packs" in payload
        names = {p["name"] for p in payload["packs"]}
        assert names == {"filesystem", "database", "http", "git", "slack", "github"}

    async def test_every_pack_has_description(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("list_packs", {})

        payload = _parse(result)
        for pack in payload["packs"]:
            assert pack["description"], f"pack {pack['name']} has no description"


# ---------------------------------------------------------------------------
# install_pack
# ---------------------------------------------------------------------------


class TestInstallPack:
    async def test_writes_files(self, tmp_path: Path) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("install_pack", {
                "name": "github",
                "dest": str(tmp_path),
            })

        assert result.isError is False
        payload = _parse(result)
        assert "files" in payload
        assert payload["dest"] == str(tmp_path)
        assert (tmp_path / "fixtures" / "github.yaml").exists()
        assert (tmp_path / "tests" / "test_github.yaml").exists()

    async def test_unknown_pack_returns_error(self, tmp_path: Path) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("install_pack", {
                "name": "does_not_exist",
                "dest": str(tmp_path),
            })

        assert result.isError is True
        payload = _parse(result)
        assert "error" in payload
        assert payload["type"] == "InstallError"


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------


class TestExplain:
    async def test_known_assertion(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("explain", {"name": "tool_called"})

        assert result.isError is False
        payload = _parse(result)
        assert payload["kind"] == "assertion"
        assert payload["docstring"]

    async def test_unknown_returns_close_matches(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("explain", {"name": "tool_cald"})

        assert result.isError is False
        payload = _parse(result)
        assert payload.get("not_found") is True
        close = payload.get("close_matches", [])
        assert "tool_called" in close

    async def test_metric_name(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("explain", {"name": "tool_efficiency"})

        payload = _parse(result)
        assert payload["kind"] == "metric"


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class TestValidate:
    async def test_valid_project_returns_ok(self, tmp_path: Path) -> None:
        shutil.copytree(QUICKSTART, tmp_path / "qs")
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("validate", {"path": str(tmp_path / "qs")})

        assert result.isError is False
        payload = _parse(result)
        assert payload["ok"] is True
        assert payload["checked"] > 0

    async def test_broken_fixture_reports_error(self, tmp_path: Path) -> None:
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "broken.yaml").write_text("server:\n  name: 123\ntools: not-a-list\n")

        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("validate", {"path": str(tmp_path)})

        payload = _parse(result)
        assert payload["ok"] is False
        assert len(payload["errors"]) > 0

    async def test_nothing_to_validate(self, tmp_path: Path) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("validate", {"path": str(tmp_path)})

        payload = _parse(result)
        assert payload["checked"] == 0
        assert payload["ok"] is True


# ---------------------------------------------------------------------------
# run_tests  (integration — runs the quickstart agent for real)
# ---------------------------------------------------------------------------


class TestRunTests:
    async def test_quickstart_passes(self, tmp_path: Path) -> None:
        shutil.copytree(QUICKSTART, tmp_path / "qs")
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("run_tests", {
                "path": str(tmp_path / "qs" / "tests"),
            })

        assert result.isError is False
        payload = _parse(result)
        assert payload["total"] == 2
        assert payload["passed"] == 2
        assert payload["failed"] == 0

    async def test_no_tests_found_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("run_tests", {
                "path": str(tmp_path / "tests"),
            })

        payload = _parse(result)
        assert payload["total"] == 0


# ---------------------------------------------------------------------------
# snapshot + diff_baselines  (integration)
# ---------------------------------------------------------------------------


class TestSnapshotAndDiff:
    async def test_snapshot_saves_baselines(self, tmp_path: Path) -> None:
        shutil.copytree(QUICKSTART, tmp_path / "qs")
        baseline_dir = str(tmp_path / "baselines")
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("snapshot", {
                "path": str(tmp_path / "qs" / "tests"),
                "baseline_dir": baseline_dir,
            })

        payload = _parse(result)
        assert payload["saved"] == 2
        assert payload["skipped"] == 0

    async def test_diff_after_snapshot_no_regressions(self, tmp_path: Path) -> None:
        shutil.copytree(QUICKSTART, tmp_path / "qs")
        baseline_dir = str(tmp_path / "baselines")
        tests_path = str(tmp_path / "qs" / "tests")

        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            # First: save baselines
            await session.call_tool("snapshot", {
                "path": tests_path,
                "baseline_dir": baseline_dir,
            })
            # Then: diff (same run should match)
            result = await session.call_tool("diff_baselines", {
                "path": tests_path,
                "baseline_dir": baseline_dir,
            })

        payload = _parse(result)
        assert payload["missing_baselines"] == 0
        assert payload["has_regressions"] is False

    async def test_diff_detects_regression(self, tmp_path: Path) -> None:
        """Modify a saved baseline to inject a regression and verify it is found."""
        shutil.copytree(QUICKSTART, tmp_path / "qs")
        baseline_dir = str(tmp_path / "baselines")
        tests_path = str(tmp_path / "qs" / "tests")

        from mcp.shared.memory import create_connected_server_and_client_session
        from mcptest.diff import BaselineStore
        from mcptest.runner.trace import Trace

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            # Save fresh baselines
            await session.call_tool("snapshot", {
                "path": tests_path,
                "baseline_dir": baseline_dir,
            })

        # Corrupt one baseline by replacing with an empty-trajectory trace
        store = BaselineStore(baseline_dir)
        ids = store.list_ids()
        first_id = ids[0]
        # Reconstruct suite/case from the baseline id filename to load+overwrite
        baseline_path = Path(baseline_dir) / f"{first_id}.json"
        bad_trace = Trace(output="completely different output xyz")
        bad_trace.save(baseline_path)

        async with create_connected_server_and_client_session(build_server()) as session:
            result = await session.call_tool("diff_baselines", {
                "path": tests_path,
                "baseline_dir": baseline_dir,
            })

        payload = _parse(result)
        assert payload["has_regressions"] is True


# ---------------------------------------------------------------------------
# conformance  (integration)
# ---------------------------------------------------------------------------


class TestConformance:
    async def test_against_fixture_yaml(self) -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("conformance", {
                "target": str(EXAMPLE_FIXTURE),
            })

        assert result.isError is False
        payload = _parse(result)
        assert "results" in payload
        assert "counts" in payload
        assert isinstance(payload["results"], list)
        assert len(payload["results"]) > 0
        counts = payload["counts"]
        assert "total" in counts and "passed" in counts

    async def test_with_severity_filter(self) -> None:
        """Exercises the severity-filter code path (lines 400-406 in server.py)."""
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("conformance", {
                "target": str(EXAMPLE_FIXTURE),
                "severity": "MUST",
            })

        assert result.isError is False
        payload = _parse(result)
        assert "results" in payload
        # With MUST-only filter some checks may be skipped
        for r in payload["results"]:
            assert r.get("severity") in ("MUST", "skipped", None) or r.get("skipped")


# ---------------------------------------------------------------------------
# coverage  (integration)
# ---------------------------------------------------------------------------


class TestCoverage:
    async def test_quickstart_computes_score(self, tmp_path: Path) -> None:
        shutil.copytree(QUICKSTART, tmp_path / "qs")
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("coverage", {
                "path": str(tmp_path / "qs"),
            })

        assert result.isError is False
        payload = _parse(result)
        assert "overall_score" in payload
        assert 0.0 <= float(payload["overall_score"]) <= 1.0

    async def test_threshold_flag(self, tmp_path: Path) -> None:
        shutil.copytree(QUICKSTART, tmp_path / "qs")
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("coverage", {
                "path": str(tmp_path / "qs"),
                "threshold": 0.0,
            })

        payload = _parse(result)
        assert "above_threshold" in payload
        assert payload["above_threshold"] is True  # 0.0 threshold always passes

    async def test_no_fixtures_dir(self, tmp_path: Path) -> None:
        """Exercises the fixture_dir.exists() -> False branch in _coverage."""
        (tmp_path / "tests").mkdir()
        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("coverage", {"path": str(tmp_path)})

        assert result.isError is False
        payload = _parse(result)
        assert "overall_score" in payload


# ---------------------------------------------------------------------------
# capture  (unit — monkeypatched stub)
# ---------------------------------------------------------------------------


class TestCapture:
    async def test_dry_run_stub(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path as _Path

        from mcptest.capture.runner import CaptureResult

        async def _stub_capture(server_or_command, output_dir=".", **kwargs) -> CaptureResult:
            assert server_or_command == "python my_server.py"
            assert kwargs.get("generate_tests") is True
            assert kwargs.get("samples_per_tool") == 5
            return CaptureResult(
                fixture_path=_Path(str(output_dir)) / "fixtures" / "my_server.yaml",
                test_paths=[_Path(str(output_dir)) / "tests" / "test_my_server.yaml"],
                discovery=None,  # type: ignore[arg-type]
                sampled_tools=[],
                sample_count=10,
                dry_run=False,
            )

        monkeypatch.setattr("mcptest.capture.runner.capture_server", _stub_capture)

        from mcp.shared.memory import create_connected_server_and_client_session

        srv = build_server()
        async with create_connected_server_and_client_session(srv) as session:
            result = await session.call_tool("capture", {
                "server_cmd": "python my_server.py",
                "output": str(tmp_path),
                "generate_tests": True,
                "samples_per_tool": 5,
            })

        assert result.isError is False
        payload = _parse(result)
        assert payload["tool_count"] == 0
        assert payload["sample_count"] == 10
        assert payload["dry_run"] is False
        assert len(payload["test_paths"]) == 1


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------


class TestUnknownTool:
    async def test_unknown_tool_returns_error(self) -> None:
        import mcp.types as types
        from mcp.server.lowlevel import Server

        srv = build_server()
        call_req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name="nonexistent_tool", arguments={}),
        )
        handler = srv.request_handlers[types.CallToolRequest]
        result = await handler(call_req)
        assert result.root.isError is True
        payload = json.loads(result.root.content[0].text)
        assert "error" in payload
        assert payload["type"] == "UnknownToolError"


# ---------------------------------------------------------------------------
# __main__ entry point
# ---------------------------------------------------------------------------


class TestMain:
    def test_default_name_calls_run_stdio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcptest.mcp_server import __main__ as main_mod

        calls: list[tuple] = []

        def fake_run(func, *args):
            calls.append((func, args))

        monkeypatch.setattr(main_mod.anyio, "run", fake_run)
        rc = main_mod.main([])
        assert rc == 0
        assert len(calls) == 1
        assert calls[0][1] == ("mcptest",)

    def test_custom_name_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcptest.mcp_server import __main__ as main_mod

        calls: list[tuple] = []

        def fake_run(func, *args):
            calls.append((func, args))

        monkeypatch.setattr(main_mod.anyio, "run", fake_run)
        rc = main_mod.main(["--name", "my-mcptest"])
        assert rc == 0
        assert calls[0][1] == ("my-mcptest",)

    def test_bad_flag_returns_error_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcptest.mcp_server import __main__ as main_mod

        monkeypatch.setattr(main_mod.anyio, "run", lambda *a: None)
        rc = main_mod.main(["--not-a-real-flag"])
        assert rc == 2


# ---------------------------------------------------------------------------
# Error paths — every tool's except-clause must serialise as isError=True
# ---------------------------------------------------------------------------


async def _call_tool(tool: str, args: dict) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    srv = build_server()
    async with create_connected_server_and_client_session(srv) as session:
        return await session.call_tool(tool, args)


class TestToolErrorPaths:
    """Verify that internal exceptions are converted to isError=True payloads."""

    async def test_run_tests_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "mcptest.cli.commands.execute_test_files",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        result = await _call_tool("run_tests", {"path": str(tmp_path)})
        assert result.isError is True
        assert "boom" in _parse(result)["error"]

    async def test_snapshot_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "mcptest.cli.commands._run_all_cases",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("snap-error")),
        )
        result = await _call_tool("snapshot", {"path": str(tmp_path)})
        assert result.isError is True
        assert "snap-error" in _parse(result)["error"]

    async def test_diff_baselines_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "mcptest.cli.commands._run_all_cases",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("diff-error")),
        )
        result = await _call_tool("diff_baselines", {"path": str(tmp_path)})
        assert result.isError is True
        assert "diff-error" in _parse(result)["error"]

    async def test_explain_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "mcptest.docs.terminal._build_index",
            lambda: (_ for _ in ()).throw(RuntimeError("index-error")),
        )
        result = await _call_tool("explain", {"name": "anything"})
        assert result.isError is True
        assert "index-error" in _parse(result)["error"]

    async def test_validate_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "mcptest.cli.commands.collect_validate_errors",
            lambda *a: (_ for _ in ()).throw(RuntimeError("val-error")),
        )
        result = await _call_tool("validate", {"path": str(tmp_path)})
        assert result.isError is True
        assert "val-error" in _parse(result)["error"]

    async def test_coverage_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patch the re-exported name on the package module — that's what
        # server._coverage imports at call time via `from mcptest.coverage import …`.
        monkeypatch.setattr(
            "mcptest.coverage.analyze_coverage",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("cov-error")),
        )
        result = await _call_tool("coverage", {"path": str(tmp_path)})
        assert result.isError is True
        assert "cov-error" in _parse(result)["error"]

    async def test_snapshot_skips_existing(self, tmp_path: Path) -> None:
        """Exercises the 'skip existing baseline' branch (skipped += 1)."""
        shutil.copytree(QUICKSTART, tmp_path / "qs")
        baseline_dir = str(tmp_path / "baselines")
        tests_path = str(tmp_path / "qs" / "tests")

        # First snapshot: saves 2 baselines
        result = await _call_tool("snapshot", {"path": tests_path, "baseline_dir": baseline_dir})
        assert _parse(result)["saved"] == 2

        # Second snapshot without update=True: should skip both
        result = await _call_tool("snapshot", {"path": tests_path, "baseline_dir": baseline_dir})
        payload = _parse(result)
        assert payload["skipped"] == 2
        assert payload["saved"] == 0

    async def test_diff_baselines_missing_baseline(self, tmp_path: Path) -> None:
        """Exercises the missing_baselines counter (no snapshot run first)."""
        shutil.copytree(QUICKSTART, tmp_path / "qs")
        empty_baseline_dir = str(tmp_path / "empty_baselines")

        result = await _call_tool("diff_baselines", {
            "path": str(tmp_path / "qs" / "tests"),
            "baseline_dir": empty_baseline_dir,
        })
        payload = _parse(result)
        assert payload["missing_baselines"] == 2
        assert payload["has_regressions"] is False
