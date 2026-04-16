# mcptest quickstart — 60 seconds to green

```bash
git clone https://github.com/josephgec/mcptest
cd mcptest/examples/quickstart

pip install mcptest
mcptest run
```

Expected output:

```
                        mcptest results
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳──────────────────────┓
┃ Suite       ┃ Case                   ┃ Status ┃ Details              ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇──────────────────────┩
│ hello suite │ agent greets the world │  PASS  │ ✓ tool_called: greet │
│ hello suite │ agent says goodbye     │  PASS  │ ✓ tool_called: fare… │
└─────────────┴────────────────────────┴────────┴──────────────────────┘

2 passed, 0 failed (2 total)
```

## What just happened

`mcptest run` did four things:

1. Read `tests/test_hello.yaml` and resolved `fixtures/hello.yaml`.
2. Spawned a **real MCP server** from the fixture YAML — no code, just declarative
   tool definitions and canned responses.
3. Executed `agent.py` (a tiny MCP client) against that mock and captured every
   tool call into a trajectory trace.
4. Ran the YAML assertions against the trace: `tool_called`, `param_matches`,
   `max_tool_calls`, `no_errors`.

## The four files

| File | What it is |
|---|---|
| `fixtures/hello.yaml` | Declares the mock server: two tools (`greet`, `farewell`) with canned responses. |
| `tests/test_hello.yaml` | Two test cases with assertions about which tools should be called. |
| `agent.py` | A ~40-line MCP stdio client. **Replace this with your real agent** — everything else keeps working. |
| `README.md` | This file. |

## Next steps

- Edit `fixtures/hello.yaml` to model your own MCP server.
- Edit `tests/test_hello.yaml` to add more cases. See `mcptest explain tool_called`
  for the full assertion catalog (`mcptest docs` for the whole reference).
- Wire `mcptest run --ci` into CI — it exits non-zero on regression.
- Snapshot a baseline (`mcptest snapshot`) and detect drift with `mcptest diff`
  when you change prompts or swap models.

Full docs: <https://github.com/josephgec/mcptest>
