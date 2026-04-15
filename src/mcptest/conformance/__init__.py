"""MCP server conformance testing.

Verifies that an MCP server implementation correctly implements the protocol
across 19 checks in 5 sections.

Quick start::

    from mcptest.conformance import InProcessServer, ConformanceRunner
    from mcptest.mock_server.server import MockMCPServer
    from mcptest.fixtures.loader import load_fixture

    fixture = load_fixture("my_server.yaml")
    mock = MockMCPServer(fixture)
    server = InProcessServer(mock=mock, fixture=fixture)

    runner = ConformanceRunner(server=server)
    import anyio
    results = anyio.from_thread.run_sync(runner.run)
"""

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
from mcptest.conformance.server import (
    InProcessServer,
    ServerUnderTest,
    StdioServer,
    make_stdio_server,
)

__all__ = [
    "CHECKS",
    "CheckOutcome",
    "ConformanceCheck",
    "ConformanceResult",
    "ConformanceRunner",
    "InProcessServer",
    "ServerUnderTest",
    "Severity",
    "StdioServer",
    "conformance_check",
    "make_stdio_server",
    "render_conformance_report",
]
