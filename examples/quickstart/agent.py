"""A tiny MCP client agent for the mcptest quickstart.

It talks real MCP (stdio) to whatever mock servers the mcptest runner
spawned, reads `MCPTEST_FIXTURES` to know which to connect to, and picks
tools from stdin with a trivial rule-based parser. Replace this with
your real agent and the same YAML tests will work unchanged.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def _run(fixture_path: str, user_input: str) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcptest.mock_server", fixture_path],
        env=os.environ.copy(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if user_input.startswith("greet "):
                name = user_input.split(" ", 1)[1].strip()
                await session.call_tool("greet", arguments={"name": name})
            elif user_input.strip() == "farewell":
                await session.call_tool("farewell", arguments={})


async def _amain() -> int:
    fixtures = json.loads(os.environ.get("MCPTEST_FIXTURES", "[]"))
    user_input = sys.stdin.read().strip()
    for fixture in fixtures:
        await _run(fixture, user_input)
    print(f"agent processed: {user_input!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
