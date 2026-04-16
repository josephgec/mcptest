"""CLI entry point for running the mcptest MCP server.

Usage::

    python -m mcptest.mcp_server          # serve over stdio with default name
    python -m mcptest.mcp_server --name my-mcptest   # override server name

The server speaks MCP over stdio so any MCP client can spawn it as a
subprocess and immediately discover the 10 mcptest tools.
"""

from __future__ import annotations

import argparse
import sys

import anyio

from mcptest.mcp_server.server import run_stdio


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcptest.mcp_server")
    parser.add_argument(
        "--name",
        default="mcptest",
        help="Server name advertised in the MCP handshake (default: mcptest).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)

    anyio.run(run_stdio, ns.name)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
