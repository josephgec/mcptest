"""mcptest MCP server — 10 tools over stdio.

Each tool is a thin adapter that delegates to existing mcptest Python
functions (runner, registry, diff, coverage, conformance, docs, capture).
No logic is duplicated here; this file is purely an integration layer.

Tools
-----
run_tests       Run test suites and return pass/fail summary.
install_pack    Install a pre-built fixture pack into a directory.
list_packs      List all available fixture packs.
snapshot        Run tests and save each trace as a baseline.
diff_baselines  Compare current traces against saved baselines.
explain         Return docs for an assertion, metric, or check.
capture         Connect to a server, sample tools, write fixture YAML.
conformance     Run MCP protocol conformance checks against a server.
validate        Validate fixture + test YAML without running any agent.
coverage        Analyse fixture surface-area coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

import mcptest


# ---------------------------------------------------------------------------
# Tool input schemas — JSON Schema dicts
# ---------------------------------------------------------------------------

_TOOL_SPECS: list[tuple[str, str, dict[str, Any]]] = [
    (
        "run_tests",
        (
            "Run mcptest test suites under a path and return a pass/fail summary. "
            "Returns passed/failed/total counts plus up to 20 failing case details. "
            "For full output on large suites use 'mcptest run --json' directly."
        ),
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory containing tests/ (or path directly to tests/)."},
                "retry": {"type": "integer", "minimum": 1, "description": "Override retry count for every case."},
                "tolerance": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Override pass-rate tolerance (0.0–1.0)."},
                "parallel": {"type": "integer", "minimum": 0, "description": "Parallel workers (0 = auto-detect, 1 = serial)."},
            },
            "required": ["path"],
        },
    ),
    (
        "install_pack",
        "Install a pre-built mcptest fixture pack (github, filesystem, database, http, git, slack) into a destination directory.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Pack name: github, filesystem, database, http, git, or slack."},
                "dest": {"type": "string", "description": "Directory to install the pack into."},
                "force": {"type": "boolean", "description": "Overwrite existing files (default false)."},
            },
            "required": ["name", "dest"],
        },
    ),
    (
        "list_packs",
        "List all pre-built mcptest fixture packs with their descriptions.",
        {"type": "object", "properties": {}, "required": []},
    ),
    (
        "snapshot",
        "Run tests under a path and save each agent trajectory as a baseline for future regression diffing.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory containing tests/."},
                "baseline_dir": {"type": "string", "description": "Where to write baseline files (default: .mcptest/baselines)."},
                "update": {"type": "boolean", "description": "Overwrite existing baselines (default false)."},
            },
            "required": ["path"],
        },
    ),
    (
        "diff_baselines",
        "Run tests and compare each trajectory against its saved baseline to detect regressions.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory containing tests/."},
                "baseline_dir": {"type": "string", "description": "Directory containing baseline files (default: .mcptest/baselines)."},
                "latency_threshold_pct": {"type": "number", "description": "Report latency regressions above this percentage (default 50.0)."},
                "ci": {"type": "boolean", "description": "Return has_regressions=true as an error signal (default false)."},
            },
            "required": ["path"],
        },
    ),
    (
        "explain",
        "Return documentation for an assertion yaml_key (e.g. 'tool_called'), metric name (e.g. 'tool_efficiency'), or conformance check ID (e.g. 'INIT-001').",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Assertion key, metric name, or check ID to explain."},
            },
            "required": ["name"],
        },
    ),
    (
        "capture",
        "Connect to a live MCP server, sample its tools, and write fixture YAML (and optionally test YAML) to disk.",
        {
            "type": "object",
            "properties": {
                "server_cmd": {"type": "string", "description": "Shell command to start the MCP server (e.g. 'python my_server.py')."},
                "output": {"type": "string", "description": "Output directory for generated files."},
                "generate_tests": {"type": "boolean", "description": "Also generate test-spec YAML (default false)."},
                "samples_per_tool": {"type": "integer", "minimum": 1, "description": "Argument variations to try per tool (default 3)."},
            },
            "required": ["server_cmd", "output"],
        },
    ),
    (
        "conformance",
        "Run MCP protocol conformance checks against a server command or fixture YAML file.",
        {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Server command (e.g. 'python server.py') or path to a fixture YAML file."},
                "severity": {
                    "type": "string",
                    "enum": ["MUST", "SHOULD", "MAY"],
                    "description": "Minimum severity level to include (default: all levels).",
                },
            },
            "required": ["target"],
        },
    ),
    (
        "validate",
        "Validate fixture and test YAML files under a path without running any agent.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project root containing fixtures/ and/or tests/."},
            },
            "required": ["path"],
        },
    ),
    (
        "coverage",
        "Analyse fixture surface-area coverage: which tools, responses, and error scenarios were exercised.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project root containing fixtures/ and tests/."},
                "threshold": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Fail if overall_score is below this value."},
            },
            "required": ["path"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _ok(payload: Any) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))],
        isError=False,
    )


def _err(exc: Exception) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(
            type="text",
            text=json.dumps({"error": str(exc), "type": type(exc).__name__}, indent=2),
        )],
        isError=True,
    )


# ---------------------------------------------------------------------------
# Tool implementations — one async function per tool
# ---------------------------------------------------------------------------

async def _run_tests(args: dict[str, Any]) -> types.CallToolResult:
    from mcptest.cli.commands import execute_test_files
    from mcptest.testspec.loader import discover_test_files

    path = str(args["path"])
    retry = args.get("retry")
    tolerance = args.get("tolerance")
    parallel = args.get("parallel", 1)

    def _blocking() -> list[Any]:
        files = discover_test_files(path)
        return execute_test_files(
            files,
            parallel_workers=int(parallel),
            retry_override=int(retry) if retry is not None else None,
            tolerance_override=float(tolerance) if tolerance is not None else None,
        )

    try:
        all_results = await anyio.to_thread.run_sync(_blocking)
    except Exception as exc:
        return _err(exc)

    passed = sum(1 for r in all_results if r.passed)
    failed = sum(1 for r in all_results if not r.passed)
    failing_cases = [r.to_dict() for r in all_results if not r.passed][:20]

    return _ok({
        "passed": passed,
        "failed": failed,
        "total": len(all_results),
        "failing_cases": failing_cases,
        "truncated": failed > 20,
    })


async def _install_pack(args: dict[str, Any]) -> types.CallToolResult:
    from mcptest.registry import InstallError, install_pack

    name = str(args["name"])
    dest = str(args["dest"])
    force = bool(args.get("force", False))

    try:
        files = await anyio.to_thread.run_sync(lambda: install_pack(name, dest, force=force))
    except InstallError as exc:
        return _err(exc)

    return _ok({"files": files, "dest": dest})


async def _list_packs(_args: dict[str, Any]) -> types.CallToolResult:
    from mcptest.registry import PACKS

    packs = [
        {"name": name, "description": pack.description}
        for name, pack in sorted(PACKS.items())
    ]
    return _ok({"packs": packs})


async def _snapshot(args: dict[str, Any]) -> types.CallToolResult:
    from mcptest.cli.commands import _run_all_cases
    from mcptest.diff import BaselineStore

    path = str(args["path"])
    baseline_dir = str(args.get("baseline_dir") or ".mcptest/baselines")
    update = bool(args.get("update", False))

    def _blocking() -> tuple[int, int, list[str]]:
        store = BaselineStore(baseline_dir)
        store.ensure()
        cases = _run_all_cases(path)
        saved = 0
        skipped = 0
        saved_paths: list[str] = []
        for suite_name, case_name, trace in cases:
            if store.exists(suite_name, case_name) and not update:
                skipped += 1
                continue
            p = store.save(suite_name, case_name, trace)
            saved += 1
            saved_paths.append(str(p))
        return saved, skipped, saved_paths

    try:
        saved, skipped, paths = await anyio.to_thread.run_sync(_blocking)
    except Exception as exc:
        return _err(exc)

    return _ok({"saved": saved, "skipped": skipped, "paths": paths})


async def _diff_baselines(args: dict[str, Any]) -> types.CallToolResult:
    from mcptest.cli.commands import _run_all_cases
    from mcptest.diff import BaselineStore, diff_traces

    path = str(args["path"])
    baseline_dir = str(args.get("baseline_dir") or ".mcptest/baselines")
    latency_threshold_pct = float(args.get("latency_threshold_pct", 50.0))

    def _blocking() -> dict[str, Any]:
        store = BaselineStore(baseline_dir)
        cases = _run_all_cases(path)
        regressions: list[dict[str, Any]] = []
        missing_baselines = 0

        for suite_name, case_name, trace in cases:
            baseline = store.load(suite_name, case_name)
            if baseline is None:
                missing_baselines += 1
                continue
            diff = diff_traces(baseline, trace, latency_threshold_pct=latency_threshold_pct)
            if diff.has_regressions:
                entry = diff.to_dict()
                entry["suite"] = suite_name
                entry["case"] = case_name
                regressions.append(entry)

        return {
            "regressions": regressions,
            "missing_baselines": missing_baselines,
            "has_regressions": bool(regressions),
        }

    try:
        payload = await anyio.to_thread.run_sync(_blocking)
    except Exception as exc:
        return _err(exc)

    return _ok(payload)


async def _explain(args: dict[str, Any]) -> types.CallToolResult:
    from mcptest.docs.terminal import _build_index

    name = str(args["name"])

    def _blocking() -> dict[str, Any]:
        import difflib
        index = _build_index()
        entry = index.get(name) or index.get(name.upper()) or index.get(name.lower())

        if entry is not None:
            return {
                "name": name,
                "kind": entry["kind"],
                "docstring": entry.get("full_doc") or entry.get("short_doc") or "",
                "short_doc": entry.get("short_doc") or "",
                "fields": entry.get("fields", []),
            }

        # Not found — suggest close matches
        all_names = sorted(set(k for k in index if not k.startswith("_")))
        close = difflib.get_close_matches(name, all_names, n=5, cutoff=0.5)
        return {
            "name": name,
            "not_found": True,
            "close_matches": close,
        }

    try:
        payload = await anyio.to_thread.run_sync(_blocking)
    except Exception as exc:
        return _err(exc)

    return _ok(payload)


async def _capture(args: dict[str, Any]) -> types.CallToolResult:
    from mcptest.capture.runner import capture_server

    server_cmd = str(args["server_cmd"])
    output = str(args["output"])
    generate_tests = bool(args.get("generate_tests", False))
    samples_per_tool = int(args.get("samples_per_tool", 3))

    try:
        result = await capture_server(
            server_cmd,
            output_dir=output,
            generate_tests=generate_tests,
            samples_per_tool=samples_per_tool,
        )
    except Exception as exc:
        return _err(exc)

    return _ok({
        "fixture_path": str(result.fixture_path) if result.fixture_path else None,
        "test_paths": [str(p) for p in result.test_paths],
        "tool_count": result.tool_count,
        "sample_count": result.sample_count,
        "dry_run": result.dry_run,
    })


async def _conformance(args: dict[str, Any]) -> types.CallToolResult:
    from mcptest.conformance import ConformanceRunner, InProcessServer, Severity
    from mcptest.conformance.check import CHECKS

    target = str(args["target"])
    severity_filter = args.get("severity")

    severities: list[Severity] | None = None
    if severity_filter:
        sev_map = {s.value: s for s in Severity}
        sv = sev_map.get(str(severity_filter).upper())
        if sv is not None:
            # Include this severity and more strict ones
            order = [Severity.MUST, Severity.SHOULD, Severity.MAY]
            idx = order.index(sv)
            severities = order[: idx + 1]

    try:
        if target.endswith((".yaml", ".yml")):
            # In-process: load fixture and use MockMCPServer
            from mcptest.fixtures.loader import load_fixture
            from mcptest.mock_server.server import MockMCPServer

            def _load():
                fixture = load_fixture(target)
                mock = MockMCPServer(fixture)
                return InProcessServer(mock=mock, fixture=fixture)

            server = await anyio.to_thread.run_sync(_load)
            runner = ConformanceRunner(server=server, severities=severities)
            results = await runner.run()
        else:
            # Subprocess: parse command into (cmd, args)
            import shlex
            from mcptest.conformance import make_stdio_server

            parts = shlex.split(target)
            cmd = parts[0]
            cmd_args = parts[1:]
            server = make_stdio_server(cmd, cmd_args)
            await server.connect()
            try:
                runner = ConformanceRunner(server=server, severities=severities)
                results = await runner.run()
            finally:
                await server.close()
    except Exception as exc:
        return _err(exc)

    result_dicts = [r.to_dict() for r in results]
    counts = {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed and not r.skipped),
        "skipped": sum(1 for r in results if r.skipped),
    }

    return _ok({"results": result_dicts, "counts": counts})


async def _validate(args: dict[str, Any]) -> types.CallToolResult:
    from mcptest.cli.commands import collect_validate_errors

    path = str(args["path"])

    try:
        checked, errors = await anyio.to_thread.run_sync(
            lambda: collect_validate_errors(path)
        )
    except Exception as exc:
        return _err(exc)

    return _ok({
        "checked": checked,
        "errors": errors,
        "ok": len(errors) == 0,
    })


async def _coverage(args: dict[str, Any]) -> types.CallToolResult:
    from mcptest.coverage import analyze_coverage
    from mcptest.fixtures.loader import load_fixture
    from mcptest.runner.trace import Trace
    from mcptest.testspec.loader import discover_test_files, load_test_suite

    path = str(args["path"])
    threshold = args.get("threshold")

    def _blocking() -> dict[str, Any]:
        root = Path(path)
        fixtures = []
        fixture_dir = root / "fixtures"
        if fixture_dir.exists():
            for f in sorted(fixture_dir.glob("**/*.yaml")) + sorted(fixture_dir.glob("**/*.yml")):
                try:
                    fixtures.append(load_fixture(f))
                except Exception:
                    pass

        # Run tests to collect traces
        from mcptest.cli.commands import _run_all_cases
        from mcptest.testspec.models import TestCase

        traces: list[Trace] = []
        test_cases: list[TestCase] = []
        try:
            triples = _run_all_cases(path)
            for _suite, _case, trace in triples:
                traces.append(trace)
        except Exception:
            pass

        # Load test cases for inject_error tracking
        test_files = discover_test_files(root / "tests")
        for t in test_files:
            try:
                suite = load_test_suite(t)
                test_cases.extend(suite.cases)
            except Exception:
                pass

        report = analyze_coverage(fixtures, traces, test_cases=test_cases or None)
        result = report.to_dict()

        if threshold is not None:
            result["above_threshold"] = report.overall_score >= float(threshold)

        return result

    try:
        payload = await anyio.to_thread.run_sync(_blocking)
    except Exception as exc:
        return _err(exc)

    return _ok(payload)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_HANDLERS = {
    "run_tests": _run_tests,
    "install_pack": _install_pack,
    "list_packs": _list_packs,
    "snapshot": _snapshot,
    "diff_baselines": _diff_baselines,
    "explain": _explain,
    "capture": _capture,
    "conformance": _conformance,
    "validate": _validate,
    "coverage": _coverage,
}


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def build_server(name: str = "mcptest") -> Server:
    """Build and return an MCP low-level Server with all 10 tools registered."""
    server: Server = Server(name)

    @server.list_tools()
    async def _list() -> list[types.Tool]:
        return [
            types.Tool(
                name=tool_name,
                description=desc,
                inputSchema=schema,
            )
            for tool_name, desc, schema in _TOOL_SPECS
        ]

    @server.call_tool(validate_input=False)
    async def _call(tool_name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        handler = _HANDLERS.get(tool_name)
        if handler is None:
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=json.dumps({"error": f"unknown tool {tool_name!r}", "type": "UnknownToolError"}),
                )],
                isError=True,
            )
        return await handler(arguments or {})

    return server


async def run_stdio(name: str = "mcptest") -> None:  # pragma: no cover
    """Run the mcptest MCP server over stdio until the client disconnects."""
    import mcp.server.stdio

    srv = build_server(name)
    async with mcp.server.stdio.stdio_server() as (read, write):
        await srv.run(
            read,
            write,
            InitializationOptions(
                server_name=name,
                server_version=mcptest.__version__,
                capabilities=srv.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
