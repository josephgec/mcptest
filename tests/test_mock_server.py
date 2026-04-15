"""Unit tests for MockMCPServer."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcptest.fixtures.loader import load_fixture
from mcptest.fixtures.models import (
    ErrorSpec,
    Fixture,
    Response,
    ServerSpec,
    ToolSpec,
)
from mcptest.mock_server import (
    MockMCPServer,
    RecordedCall,
    UnknownToolError,
)
from mcptest.mock_server.recorder import CallLog


def _github_fixture() -> Fixture:
    return Fixture(
        server=ServerSpec(name="mock-github"),
        tools=[
            ToolSpec(
                name="create_issue",
                description="Create an issue",
                input_schema={
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string"},
                        "title": {"type": "string"},
                    },
                    "required": ["repo", "title"],
                },
                responses=[
                    Response.model_validate(
                        {
                            "match": {"repo": "acme/api"},
                            "return": {"issue_number": 42, "url": "u1"},
                        }
                    ),
                    Response.model_validate(
                        {"default": True, "return": {"issue_number": 1, "url": "u2"}}
                    ),
                ],
            ),
            ToolSpec(
                name="list_issues",
                responses=[Response.model_validate({"return": {"issues": []}})],
            ),
            ToolSpec(
                name="slow_lookup",
                responses=[
                    Response.model_validate(
                        {"delay_ms": 50, "return_text": "slow"}
                    )
                ],
            ),
            ToolSpec(
                name="text_only",
                responses=[Response(return_text="plain response")],
            ),
            ToolSpec(
                name="always_rate_limited",
                responses=[Response(error="rate_limited")],
            ),
        ],
        errors=[
            ErrorSpec(
                name="rate_limited",
                tool="create_issue",
                error_code=-32000,
                message="GitHub rate limit exceeded",
            ),
            ErrorSpec(
                name="any_error",
                error_code=-32001,
                message="generic error",
            ),
        ],
    )


class TestListTools:
    async def test_lists_all_fixture_tools(self) -> None:
        server = MockMCPServer(_github_fixture())
        tools = server.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "create_issue",
            "list_issues",
            "slow_lookup",
            "text_only",
            "always_rate_limited",
        }

    async def test_tool_carries_input_schema(self) -> None:
        server = MockMCPServer(_github_fixture())
        tools = {t.name: t for t in server.list_tools()}
        assert tools["create_issue"].inputSchema["required"] == ["repo", "title"]

    async def test_empty_description_becomes_none(self) -> None:
        server = MockMCPServer(_github_fixture())
        tools = {t.name: t for t in server.list_tools()}
        assert tools["list_issues"].description is None


class TestHandleCall:
    async def test_matched_response(self) -> None:
        server = MockMCPServer(_github_fixture())
        result = await server.handle_call(
            "create_issue", {"repo": "acme/api", "title": "bug"}
        )
        assert result.isError is False
        assert result.structuredContent == {"issue_number": 42, "url": "u1"}
        assert len(server.call_log) == 1
        call = server.call_log.calls[0]
        assert call.tool == "create_issue"
        assert call.is_error is False
        assert call.result == {"issue_number": 42, "url": "u1"}

    async def test_fallback_response(self) -> None:
        server = MockMCPServer(_github_fixture())
        result = await server.handle_call(
            "create_issue", {"repo": "other/repo", "title": "x"}
        )
        assert result.isError is False
        assert result.structuredContent == {"issue_number": 1, "url": "u2"}

    async def test_text_response(self) -> None:
        server = MockMCPServer(_github_fixture())
        result = await server.handle_call("text_only", {})
        assert result.isError is False
        assert result.structuredContent is None
        assert result.content[0].text == "plain response"

    async def test_unknown_tool_raises(self) -> None:
        server = MockMCPServer(_github_fixture())
        with pytest.raises(UnknownToolError):
            await server.handle_call("nonexistent", {})
        assert len(server.call_log) == 1
        assert server.call_log.calls[0].error_code == -32601

    async def test_none_arguments_treated_as_empty(self) -> None:
        server = MockMCPServer(_github_fixture())
        result = await server.handle_call("list_issues", None)
        assert result.isError is False

    async def test_error_response_declared_by_tool(self) -> None:
        server = MockMCPServer(_github_fixture())
        result = await server.handle_call("always_rate_limited", {})
        assert result.isError is True
        assert "rate limit" in result.content[0].text.lower()
        assert server.call_log.calls[0].error_code == -32000

    async def test_no_match_returns_error_result(self) -> None:
        fx = Fixture(
            server=ServerSpec(name="s"),
            tools=[
                ToolSpec(
                    name="strict",
                    responses=[
                        Response.model_validate(
                            {"match": {"x": 1}, "return_text": "only x=1"}
                        )
                    ],
                )
            ],
        )
        server = MockMCPServer(fx)
        result = await server.handle_call("strict", {"x": 2})
        assert result.isError is True
        assert server.call_log.calls[0].error_code == -32602

    async def test_delay_honored(self) -> None:
        server = MockMCPServer(_github_fixture())
        import time

        started = time.monotonic()
        result = await server.handle_call("slow_lookup", {})
        elapsed = time.monotonic() - started
        assert result.isError is False
        assert elapsed >= 0.04  # ~50ms minus scheduler slop

    async def test_delay_skipped_when_disabled(self) -> None:
        server = MockMCPServer(_github_fixture(), honor_delays=False)
        import time

        started = time.monotonic()
        await server.handle_call("slow_lookup", {})
        assert (time.monotonic() - started) < 0.04

    async def test_call_log_accumulates(self) -> None:
        server = MockMCPServer(_github_fixture())
        await server.handle_call("list_issues", {})
        await server.handle_call("text_only", {})
        assert len(server.call_log) == 2
        assert [c.index for c in server.call_log] == [0, 1]
        assert [c.tool for c in server.call_log] == ["list_issues", "text_only"]

    async def test_shared_call_log(self) -> None:
        log = CallLog()
        s1 = MockMCPServer(_github_fixture(), call_log=log)
        s2 = MockMCPServer(_github_fixture(), call_log=log)
        await s1.handle_call("list_issues", {})
        await s2.handle_call("text_only", {})
        assert len(log) == 2


class TestErrorInjection:
    async def test_inject_then_normal_tool_produces_error(self) -> None:
        server = MockMCPServer(_github_fixture())
        server.inject_error("any_error")
        result = await server.handle_call("list_issues", {})
        assert result.isError is True
        assert "generic" in result.content[0].text

    async def test_inject_persists_across_calls(self) -> None:
        server = MockMCPServer(_github_fixture())
        server.inject_error("any_error")
        r1 = await server.handle_call("list_issues", {})
        r2 = await server.handle_call("text_only", {})
        assert r1.isError and r2.isError

    async def test_clear_injection_restores_normal(self) -> None:
        server = MockMCPServer(_github_fixture())
        server.inject_error("any_error")
        await server.handle_call("list_issues", {})
        server.clear_injection()
        r = await server.handle_call("list_issues", {})
        assert r.isError is False

    async def test_tool_scoped_injection_only_fires_for_that_tool(self) -> None:
        server = MockMCPServer(_github_fixture())
        server.inject_error("rate_limited")  # scoped to create_issue
        r1 = await server.handle_call(
            "create_issue", {"repo": "acme/api", "title": "x"}
        )
        r2 = await server.handle_call("list_issues", {})
        assert r1.isError is True
        assert r2.isError is False

    async def test_inject_unknown_error_raises(self) -> None:
        server = MockMCPServer(_github_fixture())
        with pytest.raises(ValueError, match="not declared"):
            server.inject_error("no_such_error")


class TestFromFixturePath:
    async def test_loads_yaml_file(self, tmp_path: Path) -> None:
        p = tmp_path / "x.yaml"
        p.write_text(
            "server: { name: s }\n"
            "tools:\n"
            "  - name: ping\n"
            "    responses:\n"
            "      - return_text: pong\n"
        )
        server = MockMCPServer.from_fixture_path(p)
        result = await server.handle_call("ping", {})
        assert result.content[0].text == "pong"


class TestLowlevelServer:
    async def test_build_returns_configured_server(self) -> None:
        server = MockMCPServer(_github_fixture())
        low = server.build_lowlevel_server()
        assert low.name == "mock-github"

    async def test_lowlevel_handlers_list_and_call(self) -> None:
        import mcp.types as types

        server = MockMCPServer(_github_fixture())
        low = server.build_lowlevel_server()

        list_req = types.ListToolsRequest(method="tools/list")
        list_handler = low.request_handlers[types.ListToolsRequest]
        result = await list_handler(list_req)
        tool_names = {t.name for t in result.root.tools}
        assert "create_issue" in tool_names

        call_req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="list_issues",
                arguments={},
            ),
        )
        call_handler = low.request_handlers[types.CallToolRequest]
        call_result = await call_handler(call_req)
        assert call_result.root.isError is False

    async def test_lowlevel_unknown_tool_returns_error_result(self) -> None:
        import mcp.types as types

        server = MockMCPServer(_github_fixture())
        low = server.build_lowlevel_server()

        call_req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name="does_not_exist", arguments={}),
        )
        call_handler = low.request_handlers[types.CallToolRequest]
        result = await call_handler(call_req)
        assert result.root.isError is True

    async def test_run_delegates_to_lowlevel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mcp.server.lowlevel import Server as LowlevelServer

        server = MockMCPServer(_github_fixture())

        captured: dict[str, object] = {}

        async def fake_run(self: LowlevelServer, read, write, opts) -> None:
            captured["name"] = self.name
            captured["read"] = read
            captured["write"] = write
            captured["server_name"] = opts.server_name

        monkeypatch.setattr(LowlevelServer, "run", fake_run)
        await server.run("FAKE_READ", "FAKE_WRITE")
        assert captured["name"] == "mock-github"
        assert captured["read"] == "FAKE_READ"
        assert captured["write"] == "FAKE_WRITE"
        assert captured["server_name"] == "mock-github"


class TestRecordedCall:
    def test_to_dict(self) -> None:
        call = RecordedCall(
            tool="t",
            arguments={"a": 1},
            result={"ok": True},
            latency_ms=12.0,
            server_name="s",
            index=3,
        )
        d = call.to_dict()
        assert d["tool"] == "t"
        assert d["index"] == 3
        assert d["result"] == {"ok": True}
        assert d["error"] is None

    def test_is_error_flag(self) -> None:
        ok = RecordedCall(tool="t", arguments={})
        bad = RecordedCall(tool="t", arguments={}, error="boom")
        assert ok.is_error is False
        assert bad.is_error is True


class TestCallLog:
    def test_append_assigns_index(self) -> None:
        log = CallLog()
        c1 = log.append(RecordedCall(tool="a", arguments={}))
        c2 = log.append(RecordedCall(tool="b", arguments={}))
        assert c1.index == 0
        assert c2.index == 1

    def test_clear(self) -> None:
        log = CallLog()
        log.append(RecordedCall(tool="a", arguments={}))
        log.clear()
        assert len(log) == 0

    def test_iter(self) -> None:
        log = CallLog()
        log.append(RecordedCall(tool="a", arguments={}))
        log.append(RecordedCall(tool="b", arguments={}))
        assert [c.tool for c in log] == ["a", "b"]


class TestMainModule:
    def test_missing_arg_returns_usage_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from mcptest.mock_server.__main__ import main

        rc = main([])
        assert rc == 2
        err = capsys.readouterr().err
        assert "usage" in err

    def test_bad_fixture_returns_load_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from mcptest.mock_server.__main__ import main

        bad = tmp_path / "bad.yaml"
        bad.write_text("foo: [unclosed\n")
        rc = main([str(bad)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "could not load" in err

    def test_runs_stdio_server_on_valid_fixture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mcptest.mock_server import __main__ as main_mod

        p = tmp_path / "x.yaml"
        p.write_text(
            "server: { name: s }\n"
            "tools:\n"
            "  - name: ping\n"
            "    responses:\n"
            "      - return_text: pong\n"
        )

        ran: dict[str, object] = {}

        def fake_run(func: object) -> None:
            ran["func"] = func

        monkeypatch.setattr(main_mod.anyio, "run", fake_run)
        rc = main_mod.main([str(p)])
        assert rc == 0
        assert callable(ran["func"])
