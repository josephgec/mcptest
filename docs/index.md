# mcptest

**pytest for MCP agents.**  A testing framework for Model Context Protocol (MCP) agents
that lets you mock MCP servers with YAML fixtures, run agents against them in isolation,
and assert against the resulting tool-call trajectories.

```bash
pip install mcptest
mcptest capture "python my_server.py" --output fixtures/ --generate-tests
mcptest run
```

## Features

| Feature | Description |
|---------|-------------|
| **Capture** | Auto-generate fixtures and tests from a live MCP server |
| **Assertions** | 15+ trajectory assertions (tool calls, ordering, parameters, output) |
| **Metrics** | 7 quality metrics (efficiency, redundancy, stability, …) |
| **Conformance** | 19 protocol checks across 5 sections (MUST / SHOULD / MAY) |
| **Watch mode** | Smart file watching — re-run only affected tests on save |
| **CI/CD** | GitHub Action, PR comment bot, badge generation |
| **Cloud backend** | Store traces and metrics history |
| **Parallel runs** | `-j N` for concurrent test execution |

## Quick navigation

- [Getting Started](getting-started.md) — 5-minute quickstart using `mcptest capture`
- [Assertions Reference](reference/assertions.md) — every assertion with examples
- [Metrics Reference](reference/metrics.md) — quality metrics guide
- [CLI Reference](reference/cli.md) — all commands documented

## Inline help

```bash
# Look up any assertion, metric, or check
mcptest explain tool_called
mcptest explain tool_efficiency
mcptest explain INIT-001

# List everything
mcptest docs list

# Build the full documentation site
mcptest docs build --output ./site
```
