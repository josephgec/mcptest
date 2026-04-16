"""A generic scripted MCP agent used by mcptest's built-in packs.

It reads the fixtures the runner exported via `MCPTEST_FIXTURES`, spawns
each one as an MCP stdio server, and executes whatever tool calls the
stdin input describes. The input grammar is intentionally trivial:

    tool_name [key=value] [key="value with spaces"] ...
    # one call per line or semicolon-separated; blank lines ignored

This is *not* a real agent — it doesn't reason about anything. It exists
so pack users can verify their fixture wiring with a single
`mcptest run` and then swap in their own agent without touching the pack.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


FIXTURES_ENV = "MCPTEST_FIXTURES"


def _coerce(value: str) -> Any:
    """Best-effort convert `key=value` RHS into int/float/bool/JSON/string."""
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def parse_calls(user_input: str) -> list[tuple[str, dict[str, Any]]]:
    """Turn a free-form input string into a list of `(tool, args)` pairs."""
    calls: list[tuple[str, dict[str, Any]]] = []
    for raw_line in user_input.replace(";", "\n").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        if not tokens:
            continue
        tool = tokens[0]
        args: dict[str, Any] = {}
        for tok in tokens[1:]:
            if "=" not in tok:
                continue
            key, _, val = tok.partition("=")
            args[key] = _coerce(val)
        calls.append((tool, args))
    return calls


async def _run_session(
    fixture_path: str,
    calls: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcptest.mock_server", fixture_path],
        env=os.environ.copy(),
    )
    outputs: list[str] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
            available = {t.name for t in listing.tools}
            for tool, args in calls:
                if tool not in available:
                    continue
                result = await session.call_tool(tool, arguments=args)
                if result.content:
                    text = getattr(result.content[0], "text", "") or ""
                    if text:
                        outputs.append(text)
    return outputs


async def _amain() -> int:
    try:
        fixtures = json.loads(os.environ.get(FIXTURES_ENV, "[]"))
    except json.JSONDecodeError:
        print(f"bad {FIXTURES_ENV} value", file=sys.stderr)
        return 2

    user_input = sys.stdin.read()
    calls = parse_calls(user_input)

    all_outputs: list[str] = []
    for fixture in fixtures:
        all_outputs.extend(await _run_session(fixture, calls))

    print(" | ".join(all_outputs) if all_outputs else "(no tool output)")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
