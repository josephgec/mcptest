# mcptest as an MCP Server

`mcptest` ships a built-in MCP stdio server that exposes its entire toolbox to any MCP client — Claude Code, Cursor, Continue, or any other client that speaks the Model Context Protocol.

Once wired up, you can run, diff, explain, and install pack tests from a chat window without leaving your editor.

---

## Install

`mcp>=1.0.0` is already a dependency of `mcp-agent-test`, so nothing extra is needed:

```bash
pip install mcp-agent-test
# or
uv add mcp-agent-test
```

---

## Claude Code setup

Add the following to `.mcp.json` at your project root (Claude Code discovers this automatically):

```json
{
  "mcpServers": {
    "mcptest": {
      "command": "python",
      "args": ["-m", "mcptest.mcp_server"],
      "env": {}
    }
  }
}
```

Or point at a specific venv Python:

```json
{
  "mcpServers": {
    "mcptest": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "mcptest.mcp_server"]
    }
  }
}
```

---

## Cursor / other MCP clients

Add the same block to `~/.cursor/mcp.json` (or your client's MCP config file):

```json
{
  "mcpServers": {
    "mcptest": {
      "command": "python",
      "args": ["-m", "mcptest.mcp_server"]
    }
  }
}
```

---

## Tool catalog

| Tool | What it does |
|---|---|
| `run_tests` | Run test suites under a path; returns pass/fail counts + failing case details |
| `install_pack` | Install a pre-built fixture pack (github, filesystem, database, http, git, slack) |
| `list_packs` | List all available fixture packs with descriptions |
| `snapshot` | Run tests and save each agent trajectory as a baseline |
| `diff_baselines` | Compare current trajectories against saved baselines to detect regressions |
| `explain` | Return docs for an assertion key, metric name, or conformance check ID |
| `capture` | Connect to a live MCP server, sample its tools, write fixture YAML |
| `conformance` | Run MCP protocol conformance checks against a server command or fixture YAML |
| `validate` | Validate fixture + test YAML without running any agent |
| `coverage` | Analyse fixture surface-area coverage |

---

## Example session

```
User: test my agent at ./my-project

Claude calls: list_packs {}
→ {"packs": [{"name": "github", ...}, {"name": "filesystem", ...}, ...]}

Claude calls: install_pack {"name": "filesystem", "dest": "./my-project"}
→ {"files": ["fixtures/filesystem.yaml", "tests/test_filesystem.yaml"], "dest": "./my-project"}

Claude calls: run_tests {"path": "./my-project"}
→ {"passed": 2, "failed": 0, "total": 2, "failing_cases": []}
```

---

## Troubleshooting

**Server not found**: Make sure `mcp-agent-test` is installed in the same Python environment that the MCP client will invoke. Use an absolute path to the interpreter if needed.

**"unknown tool" errors**: Restart the MCP client after installing — it caches the tool list on first connection.

**Blocking / slow responses**: `run_tests` and `snapshot` spawn agent subprocesses, which can take a few seconds per test case. This is expected; the server runs each case synchronously.

**Questions / bugs**: <https://github.com/josephgec/mcptest/issues>
