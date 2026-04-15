"""Comprehensive tests for the MCP server conformance testing module.

All tests use InProcessServer + MockMCPServer for speed and determinism.
No subprocess spawning, no network connections.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcptest.conformance.check import (
    CHECKS,
    CheckOutcome,
    ConformanceCheck,
    ConformanceResult,
    Severity,
    conformance_check,
)
from mcptest.conformance.report import render_conformance_report
from mcptest.conformance.runner import ConformanceRunner
from mcptest.conformance.server import InProcessServer, make_stdio_server
from mcptest.fixtures.models import (
    ErrorSpec,
    Fixture,
    ResourceSpec,
    Response,
    ServerSpec,
    ToolSpec,
)
from mcptest.mock_server.server import MockMCPServer


# ---------------------------------------------------------------------------
# Helpers — build Fixture objects in-process (no YAML file I/O)
# ---------------------------------------------------------------------------


def _make_fixture(
    name: str = "test-server",
    version: str = "1.0.0",
    tools: list[ToolSpec] | None = None,
    resources: list[ResourceSpec] | None = None,
    errors: list[ErrorSpec] | None = None,
) -> Fixture:
    return Fixture(
        server=ServerSpec(name=name, version=version),
        tools=tools or [],
        resources=resources or [],
        errors=errors or [],
    )


def _simple_tool(name: str = "echo", *, text: str = "hello") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"A simple {name} tool",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
        responses=[Response(return_text=text)],
    )


def _in_process_server(fixture: Fixture) -> InProcessServer:
    mock = MockMCPServer(fixture, honor_delays=False)
    return InProcessServer(mock=mock, fixture=fixture)


# ---------------------------------------------------------------------------
# CheckOutcome
# ---------------------------------------------------------------------------


class TestCheckOutcome:
    def test_passed_true(self) -> None:
        outcome = CheckOutcome(passed=True, message="ok")
        assert outcome.passed is True
        assert outcome.message == "ok"
        assert outcome.details == {}

    def test_passed_false(self) -> None:
        outcome = CheckOutcome(passed=False, message="failed")
        assert outcome.passed is False

    def test_with_details(self) -> None:
        outcome = CheckOutcome(passed=True, message="ok", details={"key": "val"})
        assert outcome.details == {"key": "val"}

    def test_details_is_plain_dict(self) -> None:
        class CustomDict(dict):
            pass
        outcome = CheckOutcome(passed=True, message="ok", details=CustomDict(x=1))
        assert type(outcome.details) is dict

    def test_frozen(self) -> None:
        outcome = CheckOutcome(passed=True, message="ok")
        with pytest.raises((AttributeError, TypeError)):
            outcome.passed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ConformanceCheck
# ---------------------------------------------------------------------------


class TestConformanceCheck:
    def test_attributes(self) -> None:
        async def _fn(server: Any) -> CheckOutcome:
            return CheckOutcome(passed=True, message="ok")

        check = ConformanceCheck(
            id="TEST-001",
            section="testing",
            name="A test check",
            severity=Severity.MUST,
            fn=_fn,
        )
        assert check.id == "TEST-001"
        assert check.section == "testing"
        assert check.severity == Severity.MUST

    def test_frozen(self) -> None:
        async def _fn(server: Any) -> CheckOutcome:
            return CheckOutcome(passed=True, message="ok")

        check = ConformanceCheck(
            id="X", section="s", name="n", severity=Severity.MAY, fn=_fn
        )
        with pytest.raises((AttributeError, TypeError)):
            check.id = "Y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ConformanceResult
# ---------------------------------------------------------------------------


class TestConformanceResult:
    def _make(self, *, passed: bool = True, skipped: bool = False) -> ConformanceResult:
        async def _fn(server: Any) -> CheckOutcome:
            return CheckOutcome(passed=True, message="ok")

        check = ConformanceCheck(
            id="T-001", section="test", name="Test check",
            severity=Severity.MUST, fn=_fn,
        )
        return ConformanceResult(
            check=check,
            passed=passed,
            message="ok" if passed else "failed",
            details={"info": "x"},
            duration_ms=1.5,
            skipped=skipped,
        )

    def test_to_dict_passed(self) -> None:
        r = self._make(passed=True)
        d = r.to_dict()
        assert d["id"] == "T-001"
        assert d["passed"] is True
        assert d["severity"] == "MUST"
        assert d["skipped"] is False
        assert d["duration_ms"] == pytest.approx(1.5, abs=0.01)

    def test_to_dict_failed(self) -> None:
        r = self._make(passed=False)
        d = r.to_dict()
        assert d["passed"] is False
        assert d["message"] == "failed"

    def test_to_dict_skipped(self) -> None:
        r = self._make(skipped=True)
        d = r.to_dict()
        assert d["skipped"] is True


# ---------------------------------------------------------------------------
# Severity enum
# ---------------------------------------------------------------------------


class TestSeverity:
    def test_values(self) -> None:
        assert Severity.MUST.value == "MUST"
        assert Severity.SHOULD.value == "SHOULD"
        assert Severity.MAY.value == "MAY"

    def test_string_comparison(self) -> None:
        assert Severity.MUST == "MUST"


# ---------------------------------------------------------------------------
# @conformance_check decorator
# ---------------------------------------------------------------------------


class TestConformanceCheckDecorator:
    def test_registers_in_checks_list(self) -> None:
        initial_count = len(CHECKS)

        @conformance_check("_DEC-001", "_test_section", "Decorator test", Severity.MAY)
        async def _dummy(server: Any) -> CheckOutcome:
            return CheckOutcome(passed=True, message="ok")

        # Should have added one more entry
        assert len(CHECKS) == initial_count + 1
        registered = CHECKS[-1]
        assert registered.id == "_DEC-001"
        assert registered.section == "_test_section"
        assert registered.severity == Severity.MAY
        assert registered.fn is _dummy

        # Cleanup: remove the test entry so it doesn't affect other tests
        CHECKS.pop()

    def test_returns_original_function(self) -> None:
        async def _fn(server: Any) -> CheckOutcome:
            return CheckOutcome(passed=True, message="ok")

        decorated = conformance_check("_DEC-002", "_test", "Test", Severity.MUST)(_fn)
        assert decorated is _fn
        CHECKS.pop()  # cleanup


# ---------------------------------------------------------------------------
# InProcessServer
# ---------------------------------------------------------------------------


class TestInProcessServer:
    @pytest.mark.asyncio
    async def test_get_server_info_name(self) -> None:
        fixture = _make_fixture(name="my-server", version="2.0.0")
        server = _in_process_server(fixture)
        info = await server.get_server_info()
        assert info["name"] == "my-server"
        assert info["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_get_capabilities_with_tools(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)
        caps = await server.get_capabilities()
        assert "tools" in caps

    @pytest.mark.asyncio
    async def test_get_capabilities_no_tools(self) -> None:
        fixture = _make_fixture()
        server = _in_process_server(fixture)
        caps = await server.get_capabilities()
        assert "tools" not in caps

    @pytest.mark.asyncio
    async def test_get_capabilities_with_resources(self) -> None:
        fixture = _make_fixture(
            resources=[ResourceSpec(uri="file:///a.txt", name="A", content="hello")]
        )
        server = _in_process_server(fixture)
        caps = await server.get_capabilities()
        assert "resources" in caps

    @pytest.mark.asyncio
    async def test_list_tools_empty(self) -> None:
        fixture = _make_fixture()
        server = _in_process_server(fixture)
        tools = await server.list_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_list_tools_single(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool("greet")])
        server = _in_process_server(fixture)
        tools = await server.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "greet"
        assert "inputSchema" in tools[0]

    @pytest.mark.asyncio
    async def test_list_tools_multiple(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool("a"), _simple_tool("b"), _simple_tool("c")])
        server = _in_process_server(fixture)
        tools = await server.list_tools()
        assert len(tools) == 3
        names = {t["name"] for t in tools}
        assert names == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_call_tool_success(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool("ping", text="pong")])
        server = _in_process_server(fixture)
        result = await server.call_tool("ping", {})
        assert "content" in result
        assert isinstance(result["content"], list)
        assert result.get("isError") is False or result.get("isError") is None

    @pytest.mark.asyncio
    async def test_call_tool_unknown_returns_error(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)
        result = await server.call_tool("__nonexistent__", {})
        assert result.get("isError") is True or "unknown" in str(result.get("content", "")).lower()

    @pytest.mark.asyncio
    async def test_list_resources_empty(self) -> None:
        fixture = _make_fixture()
        server = _in_process_server(fixture)
        resources = await server.list_resources()
        assert resources == []

    @pytest.mark.asyncio
    async def test_list_resources_populated(self) -> None:
        fixture = _make_fixture(
            resources=[
                ResourceSpec(uri="file:///a.txt", name="File A", content="aaa"),
                ResourceSpec(uri="file:///b.txt", name="File B", content="bbb"),
            ]
        )
        server = _in_process_server(fixture)
        resources = await server.list_resources()
        assert len(resources) == 2
        uris = {r["uri"] for r in resources}
        assert uris == {"file:///a.txt", "file:///b.txt"}

    @pytest.mark.asyncio
    async def test_read_resource_found(self) -> None:
        fixture = _make_fixture(
            resources=[ResourceSpec(uri="file:///doc.txt", name="Doc", content="hello doc")]
        )
        server = _in_process_server(fixture)
        result = await server.read_resource("file:///doc.txt")
        assert result["content"] == "hello doc"

    @pytest.mark.asyncio
    async def test_read_resource_not_found(self) -> None:
        fixture = _make_fixture()
        server = _in_process_server(fixture)
        result = await server.read_resource("file:///missing.txt")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_close_is_noop(self) -> None:
        fixture = _make_fixture()
        server = _in_process_server(fixture)
        await server.close()  # should not raise


# ---------------------------------------------------------------------------
# Individual check functions — pass cases
# ---------------------------------------------------------------------------


class TestInitChecksPass:
    def _server(self, **kwargs: Any) -> InProcessServer:
        return _in_process_server(_make_fixture(**kwargs))

    @pytest.mark.asyncio
    async def test_init_001_passes_with_name(self) -> None:
        from mcptest.conformance.checks import check_init_name
        outcome = await check_init_name(self._server(name="my-server"))
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_init_002_passes_with_version(self) -> None:
        from mcptest.conformance.checks import check_init_version
        outcome = await check_init_version(self._server(version="1.0.0"))
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_init_003_passes_with_caps(self) -> None:
        from mcptest.conformance.checks import check_init_capabilities
        outcome = await check_init_capabilities(self._server())
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_init_004_passes_when_tools_have_capability(self) -> None:
        from mcptest.conformance.checks import check_init_tools_capability
        outcome = await check_init_tools_capability(
            self._server(tools=[_simple_tool()])
        )
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_init_004_passes_when_no_tools(self) -> None:
        from mcptest.conformance.checks import check_init_tools_capability
        outcome = await check_init_tools_capability(self._server())
        assert outcome.passed


class TestInitChecksFail:
    @pytest.mark.asyncio
    async def test_init_001_fails_with_empty_name(self) -> None:
        from mcptest.conformance.checks import check_init_name

        class EmptyNameServer(InProcessServer):
            async def get_server_info(self) -> dict[str, str]:
                return {"name": "", "version": "1.0.0"}

        fixture = _make_fixture()
        mock = MockMCPServer(fixture)
        server = EmptyNameServer(mock=mock, fixture=fixture)
        outcome = await check_init_name(server)
        assert not outcome.passed

    @pytest.mark.asyncio
    async def test_init_002_fails_with_empty_version(self) -> None:
        from mcptest.conformance.checks import check_init_version

        class EmptyVersionServer(InProcessServer):
            async def get_server_info(self) -> dict[str, str]:
                return {"name": "srv", "version": ""}

        fixture = _make_fixture()
        mock = MockMCPServer(fixture)
        server = EmptyVersionServer(mock=mock, fixture=fixture)
        outcome = await check_init_version(server)
        assert not outcome.passed


class TestToolListingChecksPass:
    @pytest.mark.asyncio
    async def test_tool_001_passes(self) -> None:
        from mcptest.conformance.checks import check_tool_list_returns_list
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_tool_list_returns_list(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_tool_002_passes_with_schema(self) -> None:
        from mcptest.conformance.checks import check_tool_required_fields
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_tool_required_fields(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_tool_002_passes_with_no_tools(self) -> None:
        from mcptest.conformance.checks import check_tool_required_fields
        server = _in_process_server(_make_fixture())
        outcome = await check_tool_required_fields(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_tool_003_passes_unique_names(self) -> None:
        from mcptest.conformance.checks import check_tool_names_unique
        server = _in_process_server(
            _make_fixture(tools=[_simple_tool("a"), _simple_tool("b")])
        )
        outcome = await check_tool_names_unique(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_tool_004_passes_with_object_schema(self) -> None:
        from mcptest.conformance.checks import check_tool_schema_type
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_tool_schema_type(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_tool_004_passes_no_tools(self) -> None:
        from mcptest.conformance.checks import check_tool_schema_type
        server = _in_process_server(_make_fixture())
        outcome = await check_tool_schema_type(server)
        assert outcome.passed


class TestToolListingChecksFail:
    @pytest.mark.asyncio
    async def test_tool_003_fails_duplicate_names(self) -> None:
        from mcptest.conformance.checks import check_tool_names_unique

        class DupeServer(InProcessServer):
            async def list_tools(self) -> list[dict[str, Any]]:
                return [
                    {"name": "dup", "inputSchema": {"type": "object"}},
                    {"name": "dup", "inputSchema": {"type": "object"}},
                ]

        fixture = _make_fixture()
        server = DupeServer(mock=MockMCPServer(fixture), fixture=fixture)
        outcome = await check_tool_names_unique(server)
        assert not outcome.passed
        assert "dup" in str(outcome.details)

    @pytest.mark.asyncio
    async def test_tool_004_fails_non_object_schema(self) -> None:
        from mcptest.conformance.checks import check_tool_schema_type

        class NonObjectServer(InProcessServer):
            async def list_tools(self) -> list[dict[str, Any]]:
                return [{"name": "t", "inputSchema": {"type": "string"}}]

        fixture = _make_fixture()
        server = NonObjectServer(mock=MockMCPServer(fixture), fixture=fixture)
        outcome = await check_tool_schema_type(server)
        assert not outcome.passed


class TestToolCallingChecksPass:
    @pytest.mark.asyncio
    async def test_call_001_passes(self) -> None:
        from mcptest.conformance.checks import check_call_valid_tool
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_call_valid_tool(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_call_001_passes_no_tools(self) -> None:
        from mcptest.conformance.checks import check_call_valid_tool
        server = _in_process_server(_make_fixture())
        outcome = await check_call_valid_tool(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_call_002_passes_content_is_list(self) -> None:
        from mcptest.conformance.checks import check_call_result_has_content
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_call_result_has_content(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_call_003_passes_is_error_false(self) -> None:
        from mcptest.conformance.checks import check_call_success_not_error
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_call_success_not_error(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_call_004_passes_unknown_tool(self) -> None:
        from mcptest.conformance.checks import check_call_unknown_tool_error
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_call_unknown_tool_error(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_call_005_passes_is_error_true(self) -> None:
        from mcptest.conformance.checks import check_call_error_sets_is_error
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_call_error_sets_is_error(server)
        assert outcome.passed


class TestErrorHandlingChecksPass:
    @pytest.mark.asyncio
    async def test_err_001_passes_text_content(self) -> None:
        from mcptest.conformance.checks import check_error_has_text_content
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_error_has_text_content(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_err_002_passes_empty_args(self) -> None:
        from mcptest.conformance.checks import check_error_empty_args
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_error_empty_args(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_err_002_passes_no_tools(self) -> None:
        from mcptest.conformance.checks import check_error_empty_args
        server = _in_process_server(_make_fixture())
        outcome = await check_error_empty_args(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_err_003_passes_no_crash(self) -> None:
        from mcptest.conformance.checks import check_error_none_args
        server = _in_process_server(_make_fixture(tools=[_simple_tool()]))
        outcome = await check_error_none_args(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_err_003_passes_no_tools(self) -> None:
        from mcptest.conformance.checks import check_error_none_args
        server = _in_process_server(_make_fixture())
        outcome = await check_error_none_args(server)
        assert outcome.passed


class TestResourceChecksPass:
    def _resource_fixture(self) -> Fixture:
        return _make_fixture(
            resources=[
                ResourceSpec(uri="file:///a.txt", name="A", content="aaa"),
                ResourceSpec(uri="file:///b.txt", name="B", content="bbb"),
            ]
        )

    @pytest.mark.asyncio
    async def test_res_001_passes(self) -> None:
        from mcptest.conformance.checks import check_resource_list_returns_list
        server = _in_process_server(self._resource_fixture())
        outcome = await check_resource_list_returns_list(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_res_002_passes_required_fields(self) -> None:
        from mcptest.conformance.checks import check_resource_required_fields
        server = _in_process_server(self._resource_fixture())
        outcome = await check_resource_required_fields(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_res_002_passes_no_resources(self) -> None:
        from mcptest.conformance.checks import check_resource_required_fields
        server = _in_process_server(_make_fixture())
        outcome = await check_resource_required_fields(server)
        assert outcome.passed

    @pytest.mark.asyncio
    async def test_res_003_passes_unique_uris(self) -> None:
        from mcptest.conformance.checks import check_resource_uris_unique
        server = _in_process_server(self._resource_fixture())
        outcome = await check_resource_uris_unique(server)
        assert outcome.passed


class TestResourceChecksFail:
    @pytest.mark.asyncio
    async def test_res_002_fails_missing_name(self) -> None:
        from mcptest.conformance.checks import check_resource_required_fields

        class MissingNameServer(InProcessServer):
            async def list_resources(self) -> list[dict[str, Any]]:
                return [{"uri": "file:///x.txt"}]  # no 'name'

        fixture = _make_fixture()
        server = MissingNameServer(mock=MockMCPServer(fixture), fixture=fixture)
        outcome = await check_resource_required_fields(server)
        assert not outcome.passed

    @pytest.mark.asyncio
    async def test_res_003_fails_duplicate_uris(self) -> None:
        from mcptest.conformance.checks import check_resource_uris_unique

        class DupeURIServer(InProcessServer):
            async def list_resources(self) -> list[dict[str, Any]]:
                return [
                    {"uri": "file:///same.txt", "name": "A"},
                    {"uri": "file:///same.txt", "name": "B"},
                ]

        fixture = _make_fixture()
        server = DupeURIServer(mock=MockMCPServer(fixture), fixture=fixture)
        outcome = await check_resource_uris_unique(server)
        assert not outcome.passed


# ---------------------------------------------------------------------------
# ConformanceRunner
# ---------------------------------------------------------------------------


class TestConformanceRunner:
    @pytest.mark.asyncio
    async def test_run_returns_list(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server)
        results = await runner.run()
        assert isinstance(results, list)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_run_covers_all_sections(self) -> None:
        fixture = _make_fixture(
            tools=[_simple_tool()],
            resources=[ResourceSpec(uri="file:///a.txt", name="A", content="x")],
        )
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server)
        results = await runner.run()
        sections = {r.check.section for r in results}
        # With both tools and resources all 5 sections should appear
        assert "initialization" in sections
        assert "tool_listing" in sections
        assert "tool_calling" in sections
        assert "error_handling" in sections
        assert "resources" in sections

    @pytest.mark.asyncio
    async def test_filter_by_section(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server, sections=["initialization"])
        results = await runner.run()
        assert all(r.check.section == "initialization" for r in results)

    @pytest.mark.asyncio
    async def test_filter_by_multiple_sections(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)
        runner = ConformanceRunner(
            server=server, sections=["initialization", "tool_listing"]
        )
        results = await runner.run()
        assert all(r.check.section in ("initialization", "tool_listing") for r in results)

    @pytest.mark.asyncio
    async def test_filter_by_severity_must_only(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server, severities=[Severity.MUST])
        results = await runner.run()
        assert all(r.check.severity == Severity.MUST for r in results)

    @pytest.mark.asyncio
    async def test_filter_by_severity_must_and_should(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)
        runner = ConformanceRunner(
            server=server, severities=[Severity.MUST, Severity.SHOULD]
        )
        results = await runner.run()
        assert all(r.check.severity in (Severity.MUST, Severity.SHOULD) for r in results)

    @pytest.mark.asyncio
    async def test_resource_checks_skipped_when_no_resources(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])  # no resources
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server)
        results = await runner.run()
        resource_results = [r for r in results if r.check.section == "resources"]
        assert all(r.skipped for r in resource_results)

    @pytest.mark.asyncio
    async def test_resource_checks_run_when_resources_present(self) -> None:
        fixture = _make_fixture(
            tools=[_simple_tool()],
            resources=[ResourceSpec(uri="file:///x.txt", name="X", content="x")],
        )
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server)
        results = await runner.run()
        resource_results = [r for r in results if r.check.section == "resources"]
        assert any(not r.skipped for r in resource_results)

    @pytest.mark.asyncio
    async def test_check_exception_becomes_failure(self) -> None:
        """A check that raises an exception should produce a failed result, not crash."""

        class RaisingServer(InProcessServer):
            async def get_server_info(self) -> dict[str, str]:
                raise RuntimeError("simulated server error")

        fixture = _make_fixture()
        mock = MockMCPServer(fixture)
        server = RaisingServer(mock=mock, fixture=fixture)
        runner = ConformanceRunner(server=server, sections=["initialization"])
        results = await runner.run()
        # At least the init checks should have failures due to the exception
        failed = [r for r in results if not r.passed and not r.skipped]
        assert len(failed) > 0

    @pytest.mark.asyncio
    async def test_results_have_duration(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server)
        results = await runner.run()
        non_skipped = [r for r in results if not r.skipped]
        assert all(r.duration_ms >= 0.0 for r in non_skipped)

    @pytest.mark.asyncio
    async def test_all_checks_pass_for_compliant_server(self) -> None:
        """A well-formed server with tools + resources should pass all MUST checks."""
        fixture = _make_fixture(
            tools=[_simple_tool()],
            resources=[ResourceSpec(uri="file:///a.txt", name="A", content="hi")],
        )
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server, severities=[Severity.MUST])
        results = await runner.run()
        failures = [r for r in results if not r.passed and not r.skipped]
        assert failures == [], [r.message for r in failures]


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


class TestRenderConformanceReport:
    def _results(self) -> list[ConformanceResult]:
        """Build a small set of synthetic results for rendering tests."""
        async def _fn(server: Any) -> CheckOutcome:
            return CheckOutcome(passed=True, message="ok")

        def _check(id: str, section: str, sev: Severity) -> ConformanceCheck:
            return ConformanceCheck(id=id, section=section, name=f"Check {id}", severity=sev, fn=_fn)

        return [
            ConformanceResult(
                check=_check("T-001", "testing", Severity.MUST),
                passed=True, message="passed", details={}, duration_ms=1.0,
            ),
            ConformanceResult(
                check=_check("T-002", "testing", Severity.SHOULD),
                passed=False, message="failed", details={}, duration_ms=2.0,
            ),
            ConformanceResult(
                check=_check("T-003", "testing", Severity.MAY),
                passed=True, message="ok", details={}, duration_ms=0.5,
                skipped=True,
            ),
        ]

    def test_json_output_structure(self) -> None:
        results = self._results()
        output = render_conformance_report(results, as_json=True)
        data = json.loads(output)
        assert "summary" in data
        assert "results" in data
        summary = data["summary"]
        assert summary["total"] == 3
        assert summary["passed"] == 1
        assert summary["failed"] == 1
        assert summary["skipped"] == 1

    def test_json_must_failures_count(self) -> None:
        async def _fn(server: Any) -> CheckOutcome:
            return CheckOutcome(passed=True, message="ok")

        check = ConformanceCheck(
            id="X-001", section="x", name="X", severity=Severity.MUST, fn=_fn
        )
        results = [
            ConformanceResult(
                check=check, passed=False, message="must fail",
                details={}, duration_ms=1.0,
            )
        ]
        output = render_conformance_report(results, as_json=True)
        data = json.loads(output)
        assert data["summary"]["must_failures"] == 1

    def test_json_pass_rate(self) -> None:
        async def _fn(server: Any) -> CheckOutcome:
            return CheckOutcome(passed=True, message="ok")

        def _check(sev: Severity) -> ConformanceCheck:
            return ConformanceCheck(id="X", section="x", name="X", severity=sev, fn=_fn)

        results = [
            ConformanceResult(check=_check(Severity.MUST), passed=True, message="ok", details={}, duration_ms=1.0),
            ConformanceResult(check=_check(Severity.MUST), passed=True, message="ok", details={}, duration_ms=1.0),
            ConformanceResult(check=_check(Severity.MUST), passed=False, message="no", details={}, duration_ms=1.0),
        ]
        output = render_conformance_report(results, as_json=True)
        data = json.loads(output)
        assert abs(data["summary"]["pass_rate"] - (2 / 3)) < 0.01

    def test_table_output_contains_section_name(self) -> None:
        results = self._results()
        output = render_conformance_report(results, as_json=False)
        assert "testing" in output

    def test_table_output_contains_check_ids(self) -> None:
        results = self._results()
        output = render_conformance_report(results, as_json=False)
        assert "T-001" in output
        assert "T-002" in output
        assert "T-003" in output

    def test_table_output_contains_severity_labels(self) -> None:
        results = self._results()
        output = render_conformance_report(results, as_json=False)
        assert "MUST" in output
        assert "SHOULD" in output
        assert "MAY" in output

    def test_table_empty_results(self) -> None:
        output = render_conformance_report([], as_json=False)
        # Should not raise; summary line should mention 0
        assert "0/0" in output

    def test_json_empty_results(self) -> None:
        output = render_conformance_report([], as_json=True)
        data = json.loads(output)
        assert data["summary"]["total"] == 0
        assert data["results"] == []

    def test_json_result_dict_has_required_keys(self) -> None:
        results = self._results()
        output = render_conformance_report(results, as_json=True)
        data = json.loads(output)
        for r in data["results"]:
            assert "id" in r
            assert "section" in r
            assert "name" in r
            assert "severity" in r
            assert "passed" in r
            assert "message" in r
            assert "duration_ms" in r


# ---------------------------------------------------------------------------
# Full integration: runner + real checks
# ---------------------------------------------------------------------------


class TestFullIntegration:
    @pytest.mark.asyncio
    async def test_tools_only_server_all_must_pass(self) -> None:
        """A server with only tools should pass all MUST checks."""
        fixture = _make_fixture(tools=[_simple_tool("search"), _simple_tool("fetch")])
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server, severities=[Severity.MUST])
        results = await runner.run()
        must_failures = [r for r in results if not r.passed and not r.skipped]
        assert must_failures == [], [r.message for r in must_failures]

    @pytest.mark.asyncio
    async def test_empty_server_init_checks_pass(self) -> None:
        """A server with no tools and no resources still passes init MUST checks."""
        fixture = _make_fixture()
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server, sections=["initialization"])
        results = await runner.run()
        must_failures = [r for r in results if not r.passed and not r.skipped and r.check.severity == Severity.MUST]
        assert must_failures == []

    @pytest.mark.asyncio
    async def test_report_json_round_trip(self) -> None:
        """Results produced by the runner can be serialised to JSON and back."""
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)
        runner = ConformanceRunner(server=server)
        results = await runner.run()

        json_str = render_conformance_report(results, as_json=True)
        data = json.loads(json_str)

        assert data["summary"]["total"] == len(results)
        assert len(data["results"]) == len(results)

    @pytest.mark.asyncio
    async def test_section_filter_reduces_result_count(self) -> None:
        """Filtering to one section returns fewer results than running all."""
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)

        all_runner = ConformanceRunner(server=server)
        filtered_runner = ConformanceRunner(server=server, sections=["initialization"])

        all_results = await all_runner.run()
        filtered_results = await filtered_runner.run()

        assert len(filtered_results) < len(all_results)

    @pytest.mark.asyncio
    async def test_severity_filter_reduces_result_count(self) -> None:
        """Filtering to MUST-only returns fewer results than running all."""
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process_server(fixture)

        all_runner = ConformanceRunner(server=server)
        must_runner = ConformanceRunner(server=server, severities=[Severity.MUST])

        all_results = await all_runner.run()
        must_results = await must_runner.run()

        # There are SHOULD checks in the default set, so MUST-only is smaller.
        must_only_count = sum(1 for r in all_results if r.check.severity == Severity.MUST)
        assert len(must_results) == must_only_count


# ---------------------------------------------------------------------------
# make_stdio_server helper
# ---------------------------------------------------------------------------


class TestMakeStdioServer:
    def test_parses_single_command(self) -> None:
        server = make_stdio_server("python")
        assert server.command == "python"
        assert server.args == []

    def test_parses_command_with_args(self) -> None:
        server = make_stdio_server("python my_server.py --port 9000")
        assert server.command == "python"
        assert server.args == ["my_server.py", "--port", "9000"]

    def test_empty_command_raises(self) -> None:
        with pytest.raises(ValueError):
            make_stdio_server("")

    def test_quoted_args(self) -> None:
        server = make_stdio_server('python server.py --name "my server"')
        assert "my server" in server.args


# ---------------------------------------------------------------------------
# CLI command (via CliRunner)
# ---------------------------------------------------------------------------


class TestConformanceCLI:
    def _write_fixture(self, tmp_path: Any) -> str:
        content = """
server:
  name: cli-test-server
  version: "1.0.0"
tools:
  - name: greet
    description: Says hello
    input_schema:
      type: object
      properties:
        name:
          type: string
    responses:
      - return_text: Hello!
        default: true
"""
        fixture_path = tmp_path / "fixture.yaml"
        fixture_path.write_text(content)
        return str(fixture_path)

    def test_fixture_mode_table_output(self, tmp_path: Any) -> None:
        from click.testing import CliRunner
        from mcptest.cli.commands import conformance_command

        runner = CliRunner()
        fixture = self._write_fixture(tmp_path)
        result = runner.invoke(conformance_command, ["--fixture", fixture])
        assert result.exit_code == 0, result.output
        assert "initialization" in result.output

    def test_fixture_mode_json_output(self, tmp_path: Any) -> None:
        from click.testing import CliRunner
        from mcptest.cli.commands import conformance_command

        runner = CliRunner()
        fixture = self._write_fixture(tmp_path)
        result = runner.invoke(conformance_command, ["--fixture", fixture, "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "summary" in data
        assert "results" in data

    def test_fixture_mode_section_filter(self, tmp_path: Any) -> None:
        from click.testing import CliRunner
        from mcptest.cli.commands import conformance_command

        runner = CliRunner()
        fixture = self._write_fixture(tmp_path)
        result = runner.invoke(
            conformance_command,
            ["--fixture", fixture, "--json", "--section", "initialization"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        for r in data["results"]:
            assert r["section"] == "initialization"

    def test_no_args_exits_with_error(self) -> None:
        from click.testing import CliRunner
        from mcptest.cli.commands import conformance_command

        runner = CliRunner()
        result = runner.invoke(conformance_command, [])
        assert result.exit_code != 0
