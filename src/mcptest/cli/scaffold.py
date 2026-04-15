"""Scaffold new projects with a working example.

Creates:
  fixtures/example.yaml   — a tiny mock MCP server
  tests/test_example.yaml — one test case exercising it
  examples/example_agent.py — a scripted stub agent users can swap out

The scaffold is intentionally runnable out-of-the-box so `mcptest init &&
mcptest run` is a green flow.
"""

from __future__ import annotations

from pathlib import Path


class ScaffoldError(Exception):
    """Raised when the scaffold cannot be written (e.g. existing files)."""


_EXAMPLE_FIXTURE = """\
server:
  name: mock-example
  version: "1.0"

tools:
  - name: greet
    description: Greet someone by name.
    input_schema:
      type: object
      properties:
        name: { type: string }
      required: [name]
    responses:
      - match: { name: "world" }
        return:
          message: "Hello, world!"
      - default: true
        return:
          message: "Hello, stranger."

  - name: farewell
    responses:
      - return_text: "Goodbye."
"""

_EXAMPLE_TEST = """\
name: example suite
description: A demo suite you can safely delete.
fixtures:
  - ../fixtures/example.yaml
agent:
  command: python ../examples/example_agent.py
cases:
  - name: agent greets world
    input: "greet world"
    assertions:
      - tool_called: greet
      - param_matches:
          tool: greet
          param: name
          value: world
      - max_tool_calls: 2
      - no_errors: true
"""

_EXAMPLE_AGENT = """\
\"\"\"Toy scripted agent used by `mcptest init` as a starting point.

It is NOT a real MCP client — it simply writes a RecordedCall directly
to the trace file that mcptest's runner exports via `MCPTEST_TRACE_FILE`.
Replace this with your real agent once you're past the \"hello world\" stage.
\"\"\"
from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    trace_file = os.environ.get(\"MCPTEST_TRACE_FILE\")
    user_input = sys.stdin.read().strip()

    if trace_file:
        tool = \"greet\" if user_input.startswith(\"greet\") else \"farewell\"
        args = {\"name\": user_input.split(\" \", 1)[1]} if tool == \"greet\" else {}
        record = {
            \"index\": 0,
            \"tool\": tool,
            \"server\": \"mock-example\",
            \"arguments\": args,
            \"result\": {\"message\": f\"Hello, {args.get('name', 'stranger')}!\"},
            \"error\": None,
            \"error_code\": None,
            \"latency_ms\": 1.0,
            \"timestamp\": time.time(),
        }
        with open(trace_file, \"a\", encoding=\"utf-8\") as f:
            f.write(json.dumps(record) + \"\\n\")

    print(f\"agent processed: {user_input!r}\")
    return 0


if __name__ == \"__main__\":
    raise SystemExit(main())
"""


def scaffold_project(root: Path, *, force: bool = False) -> list[str]:
    """Write scaffold files under `root`; return list of created relative paths."""
    files: dict[str, str] = {
        "fixtures/example.yaml": _EXAMPLE_FIXTURE,
        "tests/test_example.yaml": _EXAMPLE_TEST,
        "examples/example_agent.py": _EXAMPLE_AGENT,
    }

    root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    for rel, contents in files.items():
        target = root / rel
        if target.exists() and not force:
            raise ScaffoldError(
                f"{target} already exists; pass --force to overwrite"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        created.append(rel)

    return created
