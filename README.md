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
- **Regression diffing** — snapshot an agent's trajectory and detect drift when prompts,
  models, or MCP servers change.
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

## Status

Alpha. The core loop (mock server → runner → assertions → CLI) is functional; cloud
dashboard, SSE transport, and test packs are under active development. See
[the implementation plan](#) for details.

## License

MIT — see [LICENSE](LICENSE).
