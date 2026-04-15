"""ServerUnderTest protocol and concrete adapters.

Two adapters are provided:

- ``InProcessServer`` — wraps a ``MockMCPServer`` + ``Fixture`` for fast,
  deterministic in-process testing (used by the conformance check unit tests).
- ``StdioServer`` — spawns a real server subprocess and connects via the MCP
  client SDK (used by ``mcptest conformance <server_command>`` in production).
"""

from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from mcptest.fixtures.models import Fixture
    from mcptest.mock_server.server import MockMCPServer


@runtime_checkable
class ServerUnderTest(Protocol):
    """Protocol implemented by both adapters — the conformance checks use only
    these methods, which keeps them transport-agnostic."""

    async def get_server_info(self) -> dict[str, str]:
        """Return ``{"name": ..., "version": ...}`` for this server."""
        ...

    async def get_capabilities(self) -> dict[str, Any]:
        """Return the server capabilities dict (tools, resources, …)."""
        ...

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the tool-definition list as plain dicts."""
        ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Call a tool and return the raw result dict.

        The dict always has a ``content`` key (list of content blocks) and
        optionally ``isError`` and ``structuredContent``.
        """
        ...

    async def list_resources(self) -> list[dict[str, Any]]:
        """Return the resource-definition list as plain dicts."""
        ...

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read a resource by URI and return its content dict."""
        ...

    async def close(self) -> None:
        """Release any held resources (processes, streams, …)."""
        ...


# ---------------------------------------------------------------------------
# InProcessServer — wraps MockMCPServer for fast unit tests
# ---------------------------------------------------------------------------


@dataclass
class InProcessServer:
    """Wraps a ``MockMCPServer`` as a ``ServerUnderTest``.

    All operations go directly to the mock server's in-process methods — no
    stdio serialisation, no subprocess, minimal latency.

    Args:
        mock: A fully constructed ``MockMCPServer`` instance.
        fixture: The ``Fixture`` the mock was built from (used to derive
            capabilities and to serve resource metadata).
    """

    mock: MockMCPServer
    fixture: Fixture

    async def get_server_info(self) -> dict[str, str]:
        return {
            "name": self.fixture.server.name,
            "version": self.fixture.server.version,
        }

    async def get_capabilities(self) -> dict[str, Any]:
        caps: dict[str, Any] = {}
        if self.fixture.tools:
            caps["tools"] = {}
        if self.fixture.resources:
            caps["resources"] = {}
        return caps

    async def list_tools(self) -> list[dict[str, Any]]:
        tools = self.mock.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.inputSchema,
            }
            for t in tools
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        from mcptest.mock_server.server import UnknownToolError

        try:
            result = await self.mock.handle_call(name, arguments)
        except UnknownToolError as exc:
            return {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }

        content = [
            {"type": block.type, "text": getattr(block, "text", None)}
            for block in result.content
        ]
        out: dict[str, Any] = {"content": content}
        if result.isError is not None:
            out["isError"] = result.isError
        if result.structuredContent is not None:
            out["structuredContent"] = result.structuredContent
        return out

    async def list_resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": r.uri,
                "name": r.name or r.uri,
                "description": r.description,
                "mimeType": r.mime_type,
            }
            for r in self.fixture.resources
        ]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        resource = self.fixture.find_resource(uri)
        if resource is None:
            return {"error": f"resource not found: {uri}"}
        return {
            "uri": resource.uri,
            "content": resource.content,
            "mimeType": resource.mime_type,
        }

    async def close(self) -> None:  # no-op for in-process
        pass


# ---------------------------------------------------------------------------
# StdioServer — spawns a real server subprocess
# ---------------------------------------------------------------------------


@dataclass
class StdioServer:
    """Spawns a real MCP server subprocess and connects via the MCP client SDK.

    Args:
        command: The executable to run (e.g. ``"python"``).
        args: Additional arguments passed to the executable.
        env: Extra environment variables merged into the subprocess environment.
        timeout_s: Seconds to wait for the server to respond to ``initialize``.
    """

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 10.0

    _session: Any = field(default=None, init=False, repr=False)
    _process: Any = field(default=None, init=False, repr=False)
    _exit_stack: Any = field(default=None, init=False, repr=False)

    async def connect(self) -> None:
        """Start the subprocess and perform the MCP handshake."""
        import contextlib

        import anyio
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        self._exit_stack = stack

        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env or None,
        )
        read_stream, write_stream = await stack.enter_async_context(
            stdio_client(params)
        )
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        # initialize() performs the MCP handshake and returns server info
        with anyio.fail_after(self.timeout_s):
            await session.initialize()
        self._session = session

    async def get_server_info(self) -> dict[str, str]:
        info = self._session.server_info
        return {
            "name": info.name if info else "",
            "version": info.version if info else "",
        }

    async def get_capabilities(self) -> dict[str, Any]:
        caps = self._session.server_capabilities
        if caps is None:
            return {}
        # Convert pydantic model to plain dict, keeping only truthy sections
        raw: dict[str, Any] = {}
        if getattr(caps, "tools", None) is not None:
            raw["tools"] = {}
        if getattr(caps, "resources", None) is not None:
            raw["resources"] = {}
        if getattr(caps, "prompts", None) is not None:
            raw["prompts"] = {}
        return raw

    async def list_tools(self) -> list[dict[str, Any]]:
        response = await self._session.list_tools()
        tools = []
        for t in response.tools:
            tools.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema,
                }
            )
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        result = await self._session.call_tool(name, arguments)
        content = []
        for block in result.content:
            content.append(
                {"type": block.type, "text": getattr(block, "text", None)}
            )
        out: dict[str, Any] = {"content": content}
        if result.isError is not None:
            out["isError"] = result.isError
        return out

    async def list_resources(self) -> list[dict[str, Any]]:
        response = await self._session.list_resources()
        return [
            {
                "uri": str(r.uri),
                "name": r.name,
                "description": getattr(r, "description", None),
                "mimeType": getattr(r, "mimeType", None),
            }
            for r in response.resources
        ]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        from mcp.types import AnyUrl

        result = await self._session.read_resource(AnyUrl(uri))
        contents = []
        for item in result.contents:
            contents.append(
                {
                    "uri": str(item.uri),
                    "text": getattr(item, "text", None),
                    "mimeType": getattr(item, "mimeType", None),
                }
            )
        return {"contents": contents}

    async def close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None


def make_stdio_server(server_command: str, **kwargs: Any) -> StdioServer:
    """Parse ``server_command`` into a ``StdioServer`` using shell-style splitting.

    Example::

        server = make_stdio_server("python my_server.py --port 9000")
    """
    parts = shlex.split(server_command)
    if not parts:
        raise ValueError("server_command must not be empty")
    command, *args = parts
    return StdioServer(command=command, args=args, **kwargs)
