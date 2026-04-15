"""Mock MCP server — runs a `Fixture` as a real MCP server.

The `MockMCPServer` turns a parsed fixture into a live MCP server that speaks
the real MCP protocol. It registers the fixture's tools with the SDK's
low-level `Server`, and for every incoming `tools/call` request it:

1. Looks up the matching `Response` rule from the fixture.
2. Honors simulated latency (`delay_ms`).
3. Produces a structured success result, or an MCP-level error result with
   the user-configured message and numeric code.
4. Appends a `RecordedCall` to the shared `CallLog`.

Tests can drive the server in two ways:

- **In-process**: call `await server.handle_call(name, args)` directly. This
  skips stdio marshalling and is the right choice for unit tests.
- **Subprocess**: run `python -m mcptest.mock_server FIXTURE.yaml` and connect
  a real MCP client. Session 3's test runner uses this mode.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from mcptest.fixtures.models import Fixture, Response
from mcptest.mock_server.matcher import NoMatchError, match_response
from mcptest.mock_server.recorder import CallLog, RecordedCall


class MockMCPServerError(Exception):
    """Base class for mock server errors that bubble up to the caller."""


class UnknownToolError(MockMCPServerError):
    """The agent asked for a tool that the fixture does not declare."""


class MockMCPServer:
    """Fixture-driven mock MCP server."""

    def __init__(
        self,
        fixture: Fixture,
        *,
        call_log: CallLog | None = None,
        honor_delays: bool = True,
    ) -> None:
        self.fixture = fixture
        self.call_log = call_log if call_log is not None else CallLog()
        self.honor_delays = honor_delays
        self._injected_error: str | None = None

    @classmethod
    def from_fixture_path(
        cls, path: str | Path, **kwargs: Any
    ) -> MockMCPServer:
        from mcptest.fixtures.loader import load_fixture

        fixture = load_fixture(path)
        return cls(fixture, **kwargs)

    def inject_error(self, error_name: str) -> None:
        """Force the *next* matching tool call to return the named error.

        The injection sticks until `clear_injection()` is called — useful for
        "every call should fail" scenarios — but tests that want one-shot
        injection should clear it themselves after observing the first
        failure. An unknown error name raises `ValueError` so misconfigured
        tests fail loudly rather than silently passing.
        """
        if self.fixture.find_error(error_name) is None:
            raise ValueError(
                f"error {error_name!r} is not declared in fixture {self.fixture.server.name!r}"
            )
        self._injected_error = error_name

    def clear_injection(self) -> None:
        self._injected_error = None

    def list_tools(self) -> list[types.Tool]:
        return [
            types.Tool(
                name=t.name,
                description=t.description or None,
                inputSchema=t.input_schema,
            )
            for t in self.fixture.tools
        ]

    async def handle_call(
        self, name: str, arguments: dict[str, Any] | None
    ) -> types.CallToolResult:
        """Handle one tool-call request.

        Returns a `CallToolResult` directly so tests can inspect `isError`,
        `structuredContent`, and `content` without routing through the stdio
        transport.
        """
        args = arguments or {}
        started = time.monotonic()

        tool = self.fixture.find_tool(name)
        if tool is None:
            latency_ms = (time.monotonic() - started) * 1000
            self.call_log.append(
                RecordedCall(
                    tool=name,
                    arguments=args,
                    error=f"unknown tool {name!r}",
                    error_code=-32601,
                    latency_ms=latency_ms,
                    server_name=self.fixture.server.name,
                )
            )
            raise UnknownToolError(f"unknown tool {name!r}")

        # Injected errors take precedence over normal response matching.
        injected = self._consume_injection(name)
        if injected is not None:
            return await self._respond_error(name, args, injected, started)

        try:
            response = match_response(tool.responses, args)
        except NoMatchError as exc:
            latency_ms = (time.monotonic() - started) * 1000
            self.call_log.append(
                RecordedCall(
                    tool=name,
                    arguments=args,
                    error=str(exc),
                    error_code=-32602,
                    latency_ms=latency_ms,
                    server_name=self.fixture.server.name,
                )
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))],
                isError=True,
            )

        if self.honor_delays and response.delay_ms:
            await anyio.sleep(response.delay_ms / 1000)

        if response.error:
            error_spec = self.fixture.find_error(response.error)
            assert error_spec is not None  # validated at Fixture load time
            return await self._respond_error(name, args, response.error, started)

        return self._respond_success(name, args, response, started)

    def _consume_injection(self, tool_name: str) -> str | None:
        if self._injected_error is None:
            return None
        # inject_error() validated existence, so find_error cannot return None
        # here unless the fixture was mutated after the fact — out of scope.
        error_spec = self.fixture.find_error(self._injected_error)
        assert error_spec is not None
        if error_spec.tool is not None and error_spec.tool != tool_name:
            return None
        return self._injected_error

    async def _respond_error(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        error_name: str,
        started: float,
    ) -> types.CallToolResult:
        error_spec = self.fixture.find_error(error_name)
        assert error_spec is not None
        latency_ms = (time.monotonic() - started) * 1000
        self.call_log.append(
            RecordedCall(
                tool=tool_name,
                arguments=arguments,
                error=error_spec.message,
                error_code=error_spec.error_code,
                latency_ms=latency_ms,
                server_name=self.fixture.server.name,
            )
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=error_spec.message)],
            isError=True,
        )

    def _respond_success(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        response: Response,
        started: float,
    ) -> types.CallToolResult:
        if response.return_text is not None:
            text = response.return_text
            structured: dict[str, Any] | None = None
            recorded_result: Any = response.return_text
        else:
            assert response.return_value is not None
            text = json.dumps(response.return_value, indent=2, default=str)
            structured = response.return_value
            recorded_result = response.return_value

        latency_ms = (time.monotonic() - started) * 1000
        self.call_log.append(
            RecordedCall(
                tool=tool_name,
                arguments=arguments,
                result=recorded_result,
                latency_ms=latency_ms,
                server_name=self.fixture.server.name,
            )
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structuredContent=structured,
            isError=False,
        )

    def build_lowlevel_server(self) -> Server:
        """Build an `mcp.server.lowlevel.Server` bound to this mock's handlers."""
        server: Server = Server(self.fixture.server.name)

        @server.list_tools()
        async def _list() -> list[types.Tool]:
            return self.list_tools()

        @server.call_tool(validate_input=False)
        async def _call(
            name: str, arguments: dict[str, Any]
        ) -> types.CallToolResult:
            try:
                return await self.handle_call(name, arguments)
            except UnknownToolError as exc:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=str(exc))],
                    isError=True,
                )

        return server

    async def run(
        self,
        read_stream: Any,
        write_stream: Any,
    ) -> None:
        """Run as an MCP server over a pre-connected pair of anyio streams.

        This is the test-friendly entry point: callers (including `run_stdio`
        and the in-process memory-stream tests) build or acquire streams,
        then hand them here. Keeping the stdio acquisition out of this
        method makes `MockMCPServer` trivially unit-testable.
        """
        server = self.build_lowlevel_server()
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=self.fixture.server.name,
                server_version=self.fixture.server.version,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

    async def run_stdio(self) -> None:  # pragma: no cover
        """Run as a real MCP server over stdio until the client disconnects.

        Not covered by unit tests — stdio acquisition blocks on real FDs. The
        subprocess-based integration tests in Session 6 exercise this path.
        """
        import mcp.server.stdio

        async with mcp.server.stdio.stdio_server() as (read, write):
            await self.run(read, write)

    def build_sse_app(self, endpoint: str = "/messages/") -> Any:
        """Build a Starlette ASGI app exposing this mock over MCP SSE.

        The returned app has two routes:

        - `GET /sse` — clients open a long-lived SSE stream here; the
          response carries the MCP server → client messages.
        - `POST {endpoint}` — clients POST each JSON-RPC request here;
          by convention this is `/messages/`.

        Any ASGI runner (uvicorn, hypercorn, Starlette's TestClient,
        httpx.ASGITransport) can serve the app, which keeps this unit-testable.
        """
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.responses import Response
        from starlette.routing import Mount, Route

        transport = SseServerTransport(endpoint)
        lowlevel = self.build_lowlevel_server()
        init_options = InitializationOptions(
            server_name=self.fixture.server.name,
            server_version=self.fixture.server.version,
            capabilities=lowlevel.get_capabilities(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        )

        async def handle_sse(request: Any) -> Response:  # pragma: no cover
            async with transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read_stream, write_stream):
                await lowlevel.run(read_stream, write_stream, init_options)
            return Response()

        return Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse, methods=["GET"]),
                Mount(endpoint, app=transport.handle_post_message),
            ]
        )

    async def run_sse(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        endpoint: str = "/messages/",
    ) -> None:  # pragma: no cover
        """Run as an SSE HTTP server via uvicorn.

        Blocks until cancelled; uncovered for the same reason as `run_stdio`.
        Session 7's integration test exercises `build_sse_app` directly.
        """
        import uvicorn

        app = self.build_sse_app(endpoint=endpoint)
        config = uvicorn.Config(app=app, host=host, port=port, log_level="error")
        server = uvicorn.Server(config)
        await server.serve()
