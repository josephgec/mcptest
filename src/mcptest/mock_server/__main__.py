"""CLI entry point for running a mock server from a fixture file.

Usage:

    python -m mcptest.mock_server FIXTURE.yaml                  # stdio (default)
    python -m mcptest.mock_server --sse --port 8765 FIXTURE.yaml  # SSE HTTP

The test runner spawns this as a subprocess so an agent under test can connect
to it over stdio exactly the way it would connect to a real MCP server.
"""

from __future__ import annotations

import argparse
import sys

import anyio

from mcptest.mock_server.recorder import default_call_log
from mcptest.mock_server.server import MockMCPServer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcptest.mock_server")
    parser.add_argument("fixture", help="Path to fixture YAML file")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport to serve (default: stdio)",
    )
    parser.add_argument("--sse", action="store_true", help="Shortcut for --transport sse")
    parser.add_argument("--host", default="127.0.0.1", help="SSE bind host")
    parser.add_argument("--port", type=int, default=8765, help="SSE bind port")
    parser.add_argument(
        "--endpoint", default="/messages/", help="SSE POST endpoint path"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)

    transport = "sse" if ns.sse else ns.transport
    fixture_path = ns.fixture

    try:
        server = MockMCPServer.from_fixture_path(
            fixture_path, call_log=default_call_log()
        )
    except Exception as exc:
        print(f"mcptest: could not load fixture {fixture_path}: {exc}", file=sys.stderr)
        return 1

    if transport == "stdio":
        anyio.run(server.run_stdio)
    else:
        async def _run_sse() -> None:
            await server.run_sse(host=ns.host, port=ns.port, endpoint=ns.endpoint)

        anyio.run(_run_sse)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
