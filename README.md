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

## Quickstart

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

## Status

Alpha. The core loop (mock server → runner → assertions → CLI) is functional; cloud
dashboard, SSE transport, and test packs are under active development. See
[the implementation plan](#) for details.

## License

MIT — see [LICENSE](LICENSE).
