# mcptest

**pytest for MCP agents.** A testing framework for Model Context Protocol (MCP) agents
that lets you mock MCP servers with YAML fixtures, run agents against them in isolation,
and assert against the resulting tool-call trajectories.

```bash
pip install mcptest
mcptest init
mcptest run
```

## Why

Building an agent that talks to real MCP servers means:

- **Cost** — every test run spends tokens and may hit paid APIs.
- **Flakiness** — external services go down, rate-limit, or return non-deterministic data.
- **Slow feedback** — end-to-end runs take minutes, not milliseconds.
- **No regression safety** — change a prompt or swap a model and you have no way to know
  if the agent's *behavior* changed until something breaks in production.

`mcptest` gives MCP agents what `pytest` gave Python code: fast, hermetic, asserted tests
and a regression safety net.

## Core features

- **Mock MCP servers from YAML** — declare tools, responses, and error scenarios in a
  fixture file. No code required for the common case.
- **Full MCP protocol** — mocks speak real MCP over stdio (and SSE), so your agent
  connects to them the same way it connects to production servers.
- **Trajectory assertions** — assert which tools were called, in what order, with what
  parameters, how many times, and how quickly.
- **Error injection** — trigger named error scenarios to test your agent's recovery paths.
- **Metric-gated assertions & scorecards** — use any quality metric as a YAML assertion
  gate (`metric_above`, `metric_below`), compose assertions with boolean combinators
  (`all_of`, `any_of`, `none_of`, `weighted_score`), and generate a weighted quality
  report card with `mcptest scorecard` for model comparison and prompt tuning.
- **Regression diffing** — snapshot an agent's trajectory and detect drift when prompts,
  models, or MCP servers change.
- **Watch mode** — `mcptest watch` monitors your test files and fixtures, automatically
  re-running only the affected tests when anything changes. Smart dependency tracking means
  only the tests that reference a changed fixture are re-run.
- **pytest integration** — use YAML files or write Python tests with fixtures.
- **CI/CD ready** — GitHub Action + PR comment bot for regression gating.
- **Inline docs** — `mcptest explain <name>` shows Rich-formatted terminal docs for any
  assertion, metric, or check. `mcptest docs build` generates a full MkDocs site with
  auto-generated reference pages that stay in sync with code automatically.

## Capture — tests write themselves

The fastest way to get started is `mcptest capture`. Point it at any MCP server
and it auto-discovers tools, samples responses, and writes both fixture YAML and
test-spec YAML — no hand-writing required.

```bash
# 1. Install
pip install mcptest

# 2. Capture a live server → auto-generate fixture + tests
mcptest capture "python my_server.py" --output fixtures/ --generate-tests

# Generated files:
#   fixtures/my-server.yaml   ← fixture with real responses
#   fixtures/my-server-tests.yaml  ← ready-to-run test suite

# 3. Run the generated tests
mcptest run fixtures/my-server-tests.yaml

# 4. Watch mode — auto-run on save
mcptest watch --watch-extra src/
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--output` / `-o` | `.` | Directory where files are written |
| `--generate-tests` | off | Also write a test-spec YAML |
| `--samples-per-tool` | `3` | Argument variations tried per tool |
| `--dry-run` | off | Preview without writing files |
| `--agent` | `python agent.py` | Agent command embedded in test suites |

## Quickstart (manual)

```bash
# 1. Install
pip install mcptest

# 2. Scaffold a project
mcptest init

# 3. Edit fixtures/example.yaml and tests/example.yaml

# 4. Run
mcptest run

# 5. Watch mode — auto-run on save
mcptest watch --watch-extra src/
```

## Example fixture

```yaml
# fixtures/github.yaml
server:
  name: mock-github

tools:
  - name: create_issue
    description: Create a GitHub issue
    input_schema:
      type: object
      properties:
        repo: { type: string }
        title: { type: string }
      required: [repo, title]
    responses:
      - match: { repo: acme/api }
        return:
          issue_number: 42
          url: https://github.com/acme/api/issues/42
      - default: true
        return:
          issue_number: 1

errors:
  - name: rate_limited
    tool: create_issue
    error_code: -32000
    message: GitHub API rate limit exceeded
```

## Example test

```yaml
# tests/issue_triage.yaml
name: Issue triage agent
fixtures:
  - fixtures/github.yaml
agent:
  command: python examples/issue_agent.py
cases:
  - name: Creates issue for bug report
    input: "File a bug: login page 500 error on Safari"
    assertions:
      - tool_called: create_issue
      - param_matches:
          tool: create_issue
          param: title
          contains: "500"
      - max_tool_calls: 3
```

## Metric-gated assertions

Use any of the 7 built-in quality metrics directly as YAML assertion gates:

```yaml
assertions:
  # Agent must be efficient (≥80% unique tool usage)
  - metric_above: {metric: tool_efficiency, threshold: 0.8}
  # Agent must not be repetitive (non-redundancy score ≥0.9)
  - metric_above: {metric: redundancy, threshold: 0.9}
  # Gate on a weighted composite quality score
  - weighted_score:
      threshold: 0.75
      weights:
        tool_efficiency: 0.3
        redundancy: 0.2
        error_recovery_rate: 0.5
```

Boolean combinators for complex logic:

```yaml
assertions:
  - all_of:
      - tool_called: create_issue
      - max_tool_calls: 5
  - any_of:
      - tool_called: create_issue
      - output_contains: created
  - none_of:
      - tool_called: delete_all
      - output_contains: ERROR
```

## Agent scorecard

Generate a weighted quality report card from any saved trace:

```bash
# Render a human-readable table (exit 1 if composite score < 0.75)
mcptest scorecard trace.json

# Override the threshold
mcptest scorecard trace.json --fail-under 0.8

# Custom weights from a YAML config
mcptest scorecard trace.json --config scorecard.yaml

# Machine-readable JSON output (for CI pipelines)
mcptest scorecard trace.json --json
```

Example `scorecard.yaml`:

```yaml
composite_threshold: 0.75
default_threshold: 0.7
thresholds:
  tool_efficiency: 0.8
  error_recovery_rate: 0.9
weights:
  tool_efficiency: 2.0
  redundancy: 1.0
  error_recovery_rate: 3.0
```

## Conformance testing

Verify that any MCP server implementation correctly implements the protocol.
19 checks across 5 sections, each tagged with RFC 2119 severity (MUST / SHOULD / MAY).

### Quick start

```bash
# Test a server subprocess over stdio
mcptest conformance "python my_server.py"

# Test in-process using a fixture YAML (fast, no subprocess)
mcptest conformance --fixture fixtures/my_server.yaml

# Filter to a specific section
mcptest conformance --fixture fixtures/my_server.yaml --section initialization

# Only run MUST checks (CI gate — fail only on hard violations)
mcptest conformance --fixture fixtures/my_server.yaml --severity must

# Also fail on SHOULD violations
mcptest conformance --fixture fixtures/my_server.yaml --fail-on-should

# Machine-readable output for CI pipelines
mcptest conformance --fixture fixtures/my_server.yaml --json
```

### Check catalogue

| ID       | Section        | Severity | Description                                          |
|----------|----------------|----------|------------------------------------------------------|
| INIT-001 | initialization | MUST     | Server provides non-empty name                       |
| INIT-002 | initialization | MUST     | Server info includes version string                  |
| INIT-003 | initialization | MUST     | Server reports capabilities object                   |
| INIT-004 | initialization | SHOULD   | Capabilities includes `tools` when server has tools  |
| TOOL-001 | tool_listing   | MUST     | `list_tools()` returns a list                        |
| TOOL-002 | tool_listing   | MUST     | Each tool has `name` and `inputSchema` fields        |
| TOOL-003 | tool_listing   | MUST     | All tool names are unique                            |
| TOOL-004 | tool_listing   | SHOULD   | Each `inputSchema` has `type: "object"` at root      |
| CALL-001 | tool_calling   | MUST     | Calling a valid tool with matching arguments returns result |
| CALL-002 | tool_calling   | MUST     | Result contains `content` list                       |
| CALL-003 | tool_calling   | MUST     | Successful result has `isError` absent or False      |
| CALL-004 | tool_calling   | MUST     | Calling unknown tool name returns error              |
| CALL-005 | tool_calling   | SHOULD   | Error response sets `isError` to True                |
| ERR-001  | error_handling | MUST     | Error result contains text content with message      |
| ERR-002  | error_handling | SHOULD   | Server handles empty arguments dict without crashing |
| ERR-003  | error_handling | SHOULD   | Server handles None arguments without crashing       |
| RES-001  | resources      | MUST     | `list_resources()` returns a list                    |
| RES-002  | resources      | MUST     | Each resource has `uri` and `name` fields            |
| RES-003  | resources      | MUST     | Resource URIs are unique                             |

Resource checks (RES-*) are automatically skipped when the server has no `resources` capability.

### CI integration

```yaml
# .github/workflows/conformance.yml
- name: MCP conformance
  run: mcptest conformance --fixture fixtures/server.yaml --severity must --json > conformance.json
```

Exit code is 1 when any MUST check fails (or any SHOULD check fails with `--fail-on-should`).

### Programmatic usage

```python
import anyio
from mcptest.conformance import ConformanceRunner, InProcessServer, Severity
from mcptest.fixtures.loader import load_fixture
from mcptest.mock_server.server import MockMCPServer

fixture = load_fixture("fixtures/my_server.yaml")
mock = MockMCPServer(fixture)
server = InProcessServer(mock=mock, fixture=fixture)

runner = ConformanceRunner(server=server, severities=[Severity.MUST])
results = anyio.run(runner.run)
must_failures = [r for r in results if not r.passed and not r.skipped]
```

## Documentation

Full reference documentation is auto-generated from live registries so it never
goes stale.

```bash
# Look up any assertion, metric, or check inline
mcptest explain tool_called
mcptest explain tool_efficiency
mcptest explain INIT-001

# List all available assertions, metrics, and checks
mcptest docs list

# Generate a full MkDocs documentation site
mcptest docs build --output ./site
cd site && mkdocs serve
```

The generated site includes:

- **[Getting Started](docs/getting-started.md)** — capture-first 5-minute quickstart
- **[Assertions Reference](docs/reference/assertions.md)** — all 19 assertions with YAML examples
- **[Metrics Reference](docs/reference/metrics.md)** — 7 quality metrics with score interpretation
- **[Conformance Checks Reference](docs/reference/checks.md)** — 19 protocol checks with severity
- **[CLI Reference](docs/reference/cli.md)** — every command with full option tables

## Status

Alpha. The core loop (mock server → runner → assertions → CLI) is functional; cloud
dashboard, SSE transport, and test packs are under active development. See
[the implementation plan](#) for details.

## License

MIT — see [LICENSE](LICENSE).
