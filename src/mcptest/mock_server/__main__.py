"""CLI entry point: `python -m mcptest.mock_server FIXTURE.yaml`.

The test runner spawns this as a subprocess so an agent under test can connect
to it over stdio exactly the way it would connect to a real MCP server. The
runner passes the fixture path as the only argument.
"""

from __future__ import annotations

import sys

import anyio

from mcptest.mock_server.server import MockMCPServer


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m mcptest.mock_server FIXTURE.yaml", file=sys.stderr)
        return 2

    fixture_path = args[0]
    from mcptest.mock_server.recorder import default_call_log

    try:
        server = MockMCPServer.from_fixture_path(
            fixture_path, call_log=default_call_log()
        )
    except Exception as exc:
        print(f"mcptest: could not load fixture {fixture_path}: {exc}", file=sys.stderr)
        return 1

    anyio.run(server.run_stdio)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
