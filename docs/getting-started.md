# Getting Started

The fastest path from zero to running tests is `mcptest capture`.  Point it at
any MCP server and it auto-discovers tools, samples responses, and writes both
fixture YAML and test-spec YAML — no hand-writing required.

## 1. Install

```bash
pip install mcptest
```

## 2. Capture a live server

```bash
mcptest capture "python my_server.py" --output fixtures/ --generate-tests
```

This will:

1. Start your server subprocess and connect over stdio
2. Call `list_tools()` to discover all available tools
3. Sample each tool with varied arguments (`--samples-per-tool 3` by default)
4. Write `fixtures/my-server.yaml` with real server responses
5. Write `fixtures/my-server-tests.yaml` with a ready-to-run test suite

## 3. Run the generated tests

```bash
mcptest run fixtures/my-server-tests.yaml
```

## 4. Iterate with watch mode

```bash
mcptest watch --watch-extra src/
```

`mcptest watch` monitors your test files, fixtures, and source directories.
It re-runs only the tests affected by each change — perfect for tight
feedback loops during development.

## 5. Inline help

```bash
# Look up any assertion, metric, or check by name
mcptest explain tool_called
mcptest explain tool_efficiency
mcptest explain INIT-001

# List all available names
mcptest docs list
```

## Next steps

- [Writing Tests](guides/writing-tests.md) — manual YAML test authoring
- [Assertions Reference](reference/assertions.md) — all assertions with examples
- [CI Integration](guides/ci-integration.md) — GitHub Action setup
