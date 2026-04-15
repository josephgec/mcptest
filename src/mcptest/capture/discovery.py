"""Live MCP server discovery.

``ServerDiscovery`` connects to a ``ServerUnderTest`` (in-process or stdio),
interrogates its capabilities, and returns a ``DiscoveryResult`` describing
everything the server offers.

The module deliberately depends only on the ``ServerUnderTest`` protocol so it
stays transport-agnostic: callers can pass an ``InProcessServer`` in tests or a
``StdioServer`` in production with no change in behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryResult:
    """Everything learned from a single server interrogation.

    Attributes
    ----------
    server_name:
        The ``name`` field from the server's ``initialize`` response.
    server_version:
        The ``version`` field from the server's ``initialize`` response.
    capabilities:
        Raw capabilities dict (keys: ``"tools"``, ``"resources"``, etc.).
    tools:
        List of tool dicts, each with ``name``, ``description``, and
        ``inputSchema``.
    resources:
        List of resource dicts, each with ``uri``, ``name``, ``description``,
        and ``mimeType``.
    """

    server_name: str
    server_version: str
    capabilities: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_tools(self) -> bool:
        return bool(self.tools)

    @property
    def has_resources(self) -> bool:
        return bool(self.resources)

    @property
    def tool_names(self) -> list[str]:
        return [t["name"] for t in self.tools]


# ---------------------------------------------------------------------------
# Discovery class
# ---------------------------------------------------------------------------


class ServerDiscovery:
    """Interrogate a ``ServerUnderTest`` and return a :class:`DiscoveryResult`.

    Parameters
    ----------
    server:
        Any object satisfying the ``ServerUnderTest`` protocol —
        ``InProcessServer``, ``StdioServer``, or any compatible adapter.
    """

    def __init__(self, server: Any) -> None:
        self._server = server

    async def discover(self) -> DiscoveryResult:
        """Run all discovery queries and return a :class:`DiscoveryResult`.

        Queries are performed in this order:

        1. ``get_server_info()`` — server name and version
        2. ``get_capabilities()`` — supported feature sections
        3. ``list_tools()`` — tool definitions (skipped if no tools capability)
        4. ``list_resources()`` — resource definitions (skipped if no resources)

        Failures in optional steps (tools/resources enumeration) are caught and
        result in an empty list rather than an exception, so partial discovery
        is still useful.
        """
        info = await self._server.get_server_info()
        server_name = info.get("name", "")
        server_version = info.get("version", "0.1.0")

        capabilities = await self._server.get_capabilities()

        tools: list[dict[str, Any]] = []
        if "tools" in capabilities:
            try:
                tools = await self._server.list_tools()
            except Exception:
                tools = []

        resources: list[dict[str, Any]] = []
        if "resources" in capabilities:
            try:
                resources = await self._server.list_resources()
            except Exception:
                resources = []

        return DiscoveryResult(
            server_name=server_name,
            server_version=server_version,
            capabilities=capabilities,
            tools=tools,
            resources=resources,
        )
