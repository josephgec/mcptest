"""A small scripted agent that talks real MCP to mcptest mock servers.

This is not a production agent — it has zero intelligence. Given a user
input string, it:

1. Reads `MCPTEST_FIXTURES` (a JSON list of fixture paths exported by the
   Runner) and for each fixture spawns `python -m mcptest.mock_server
   FIXTURE.yaml` as an MCP stdio server.
2. Connects to each mock over the real `mcp` Python SDK `ClientSession`.
3. Runs a tiny rule-based loop that picks tools and arguments from the
   input (e.g. `greet world` → calls `greet(name="world")`; `list`
   → calls `list_issues()`).
4. Concatenates the tool outputs and prints them on stdout so the Runner
   can capture them as the final agent output.

The mock servers inherit `MCPTEST_TRACE_FILE` from us, so every tool call
lands in the shared JSONL trace file. The Runner reads it back and turns
it into a `Trace` that assertions evaluate against.

Two things make this useful:

- It's an end-to-end integration of every subsystem mcptest ships, so the
  project's own test suite can run a real agent loop without LLM calls.
- It's a short, readable template that users can copy when writing their
  own first scripted agent.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
from typing import Any

try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except ImportError as exc:  # pragma: no cover
    print(f"mcp SDK not installed: {exc}", file=sys.stderr)
    sys.exit(2)


FIXTURES_ENV = "MCPTEST_FIXTURES"


def _parse_rules(user_input: str) -> list[tuple[str, dict[str, Any]]]:
    """Turn `user_input` into a sequence of `(tool_name, args)` pairs.

    Grammar (loose):
        greet NAME        → ("greet", {"name": NAME})
        farewell          → ("farewell", {})
        list              → ("list_issues", {})
        create REPO TITLE → ("create_issue", {"repo": REPO, "title": TITLE})
        ping              → ("ping", {})

    Unknown tokens are ignored so authors can chain multiple commands with
    commas: `greet world, list`.
    """
    plan: list[tuple[str, dict[str, Any]]] = []
    for chunk in re.split(r"[,;\n]", user_input):
        tokens = shlex.split(chunk.strip())
        if not tokens:
            continue
        verb = tokens[0].lower()
        if verb == "greet" and len(tokens) >= 2:
            plan.append(("greet", {"name": tokens[1]}))
        elif verb == "farewell":
            plan.append(("farewell", {}))
        elif verb == "list":
            plan.append(("list_issues", {}))
        elif verb == "create" and len(tokens) >= 3:
            plan.append(("create_issue", {"repo": tokens[1], "title": " ".join(tokens[2:])}))
        elif verb == "ping":
            plan.append(("ping", {}))
    return plan


async def _run_session(fixture_path: str, plan: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Connect to one mock server and execute the plan against it."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcptest.mock_server", fixture_path],
        env=os.environ.copy(),
    )
    outputs: list[str] = []
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tool_listing = await session.list_tools()
            available = {t.name for t in tool_listing.tools}

            for tool_name, args in plan:
                if tool_name not in available:
                    continue
                result = await session.call_tool(tool_name, arguments=args)
                if result.content:
                    first = result.content[0]
                    text = getattr(first, "text", "") or ""
                    outputs.append(text)
    return outputs


async def _amain() -> int:
    fixtures_json = os.environ.get(FIXTURES_ENV, "[]")
    try:
        fixtures = json.loads(fixtures_json)
    except json.JSONDecodeError:
        print(f"bad {FIXTURES_ENV} value: {fixtures_json!r}", file=sys.stderr)
        return 2

    user_input = sys.stdin.read().strip()
    plan = _parse_rules(user_input)

    all_outputs: list[str] = []
    for fixture_path in fixtures:
        outputs = await _run_session(fixture_path, plan)
        all_outputs.extend(outputs)

    print(" | ".join(all_outputs) if all_outputs else "(no tool output)")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
