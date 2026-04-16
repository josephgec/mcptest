# mcptest

**pytest for MCP agents.** A vendor-neutral testing framework for Model Context Protocol (MCP)
agents that lets you mock MCP servers with YAML fixtures, run agents against them in isolation,
and assert against the resulting tool-call trajectories.

```bash
pip install mcp-agent-test
mcptest init
mcptest run
```

[![PyPI](https://img.shields.io/pypi/v/mcp-agent-test)](https://pypi.org/project/mcp-agent-test/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why

With Promptfoo acquired by OpenAI (March 2026), Humanloop by Anthropic (August 2025), and
Galileo by Cisco (mid-2026), the independent eval-tool landscape has been absorbed into
platform companies. `mcptest` fills the vacuum: a **vendor-neutral, open-source** testing
framework purpose-built for MCP agent workflows.

Building an agent that talks to real MCP servers means:

- **Cost** — every test run spends tokens and may hit paid APIs.
- **Flakiness** — external services go down, rate-limit, or return non-deterministic data.
- **Slow feedback** — end-to-end runs take minutes, not milliseconds.
- **No regression safety** — change a prompt or swap a model and you have no way to know
  if the agent's *behavior* changed until something breaks in production.

`mcptest` gives MCP agents what `pytest` gave Python code: fast, hermetic, asserted tests
and a regression safety net.

## 60-second quickstart

### Option A: Clone and run

```bash
git clone https://github.com/josephgec/mcptest
cd mcptest/examples/quickstart
pip install mcp-agent-test
mcptest run
```

You'll see:

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

### Option B: Scaffold a new project

```bash
pip install mcp-agent-test
mcptest init my-tests
cd my-tests
mcptest run
```

### Option C: Capture from a live server

```bash
pip install mcp-agent-test
mcptest capture "python my_server.py" --output fixtures/ --generate-tests
mcptest run fixtures/my-server-tests.yaml
```

## Use as an MCP server

Drop one file into your project root and every tool becomes available natively in Claude Code, Cursor, and any other MCP client:

```json
// .mcp.json
{
  "mcpServers": {
    "mcptest": { "command": "python", "args": ["-m", "mcptest.mcp_server"] }
  }
}
```

Ten tools exposed: `run_tests`, `install_pack`, `list_packs`, `snapshot`, `diff_baselines`, `explain`, `capture`, `conformance`, `validate`, `coverage`. See [docs/mcp-server.md](docs/mcp-server.md) for full setup instructions.

## Core features

| Feature | Description |
|---------|-------------|
| **YAML mock servers** | Declare tools, responses, and error scenarios — no code required |
| **Full MCP protocol** | Mocks speak real MCP over stdio and SSE/HTTP |
| **17 trajectory assertions** | `tool_called`, `param_matches`, `tool_order`, `error_handled`, `output_contains`, `metric_above`, boolean combinators, and more |
| **7 quality metrics** | `tool_efficiency`, `redundancy`, `schema_compliance`, `stability`, `tool_coverage`, `error_recovery_rate`, `trajectory_similarity` |
| **Regression diffing** | Snapshot baselines and detect drift when prompts, models, or servers change |
| **Non-determinism testing** | Retry with tolerance — run N times, pass if ≥ T% succeed, measure stability |
| **Parallel execution** | `-j N` flag for parallel test runs via `ThreadPoolExecutor` |
| **6 fixture packs** | Pre-built mocks for GitHub, Slack, filesystem, database, HTTP, and git |
| **MCP conformance testing** | 19 protocol checks across 5 sections with RFC 2119 severity |
| **Semantic evaluation** | Keyword, regex, and similarity grading — no LLM API calls needed |
| **Agent benchmarking** | Side-by-side model comparison with leaderboard and metric breakdowns |
| **Fixture coverage** | Analyse which tools and responses your tests exercise |
| **Watch mode** | Auto-rerun affected tests on file save |
| **pytest plugin** | `@pytest.mark.mcptest` decorator and YAML collection |
| **CI/CD ready** | GitHub Action + PR comment bot + badge generator |
| **Cloud dashboard** | FastAPI backend with web UI, trend charts, baselines, and webhooks |
| **Plugin system** | Custom assertions, metrics, and exporters via decorators or entry points |

## Pre-built fixture packs

Get started immediately with mocks for popular MCP server patterns:

```bash
# List available packs
mcptest list-packs

# Install one
mcptest install-pack github ./my-project
mcptest install-pack slack ./my-project

# Run it (uses the built-in generic agent)
cd my-project && mcptest run
```

| Pack | Tools | Test cases | Error scenarios |
|------|-------|------------|-----------------|
| **github** | `gh_list_issues`, `gh_create_issue`, `gh_list_pulls`, `gh_merge_pr`, `gh_get_repo` | 5 | not-found, archived, merge-conflict, draft-blocked |
| **slack** | `slack_send_message`, `slack_list_channels`, `slack_get_user` | 3 | permission-denied, channel-not-found, user-not-found |
| **filesystem** | `fs_read`, `fs_write`, `fs_list`, `fs_delete` | 3 | not-found, permission-denied, path-traversal |
| **database** | `db_query`, `db_execute`, `db_list_tables` | 3 | forbidden-DDL, connection-lost |
| **http** | `http_get`, `http_post` | 3 | rate-limited, timeout |
| **git** | `git_commit`, `git_branch`, `git_log`, `git_diff` | 3 | empty-message, branch-exists, merge-conflict |

Each pack includes a fixture YAML and a test suite with real assertions — not placeholders.
Swap the agent command in the test YAML with your own agent and everything keeps working.

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

## Regression diffing

The core differentiated feature: snapshot a baseline, change your prompt or model, diff
the trajectories.

```bash
# 1. Establish a baseline
mcptest snapshot tests/

# 2. Make changes to your agent/prompt/model

# 3. Diff against the baseline
mcptest diff --ci
# → exits non-zero if any regression detected
```

### GitHub Actions integration

```yaml
# .github/workflows/mcptest.yml
- uses: josephgec/mcptest@main
  with:
    fail_on_regression: "true"
    post_pr_comment: "true"
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

The action runs your tests, diffs against baselines, and posts a PR comment summarizing
regressions — including metric deltas and tool-call diffs.

## Non-deterministic agent testing

AI agents are non-deterministic. `mcptest` handles this natively:

```yaml
cases:
  - name: agent books a table
    input: "Book a table for 2 at 7pm"
    retry: 5           # run 5 times
    tolerance: 0.8     # pass if ≥80% of runs succeed
    assertions:
      - tool_called: create_booking
```

```bash
# Override from CLI
mcptest run --retry 5 --tolerance 0.8
```

The `RetryResult` captures per-run traces, pass rate, and **stability** (pairwise
trajectory agreement) — so you can distinguish "flaky tool selection" from "consistently
wrong."

## Parallel execution

```bash
# Run tests across 4 workers
mcptest run -j 4

# Disable for a specific suite
# (set parallel: false in the test YAML)
```

Each test case spawns an independent subprocess with a UUID-scoped trace file —
zero shared mutable state.

## Fixture coverage

Analyse which tools and responses your tests actually exercise:

```bash
# Show coverage report
mcptest coverage tests/

# CI gate: fail if coverage < 80%
mcptest coverage tests/ --threshold 0.8
```

Coverage scores are weighted: 40% tool coverage + 40% response path coverage + 20%
error scenario coverage.

## Metric-gated assertions

Use any of the 7 built-in quality metrics directly as YAML assertion gates:

```yaml
assertions:
  - metric_above: {metric: tool_efficiency, threshold: 0.8}
  - metric_above: {metric: redundancy, threshold: 0.9}
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

```bash
mcptest scorecard trace.json
mcptest scorecard trace.json --fail-under 0.8
mcptest scorecard trace.json --config scorecard.yaml --json
```

## Conformance testing

Verify that any MCP server implementation correctly implements the protocol.
19 checks across 5 sections, each tagged with RFC 2119 severity (MUST / SHOULD / MAY).

```bash
# Test a server subprocess over stdio
mcptest conformance "python my_server.py"

# Test in-process using a fixture YAML
mcptest conformance --fixture fixtures/my_server.yaml

# Only run MUST checks (CI gate)
mcptest conformance --fixture fixtures/my_server.yaml --severity must

# Machine-readable JSON
mcptest conformance --fixture fixtures/my_server.yaml --json
```

| ID | Section | Severity | Description |
|----|---------|----------|-------------|
| INIT-001 | initialization | MUST | Server provides non-empty name |
| INIT-002 | initialization | MUST | Server info includes version string |
| INIT-003 | initialization | MUST | Server reports capabilities object |
| INIT-004 | initialization | SHOULD | Capabilities includes `tools` when server has tools |
| TOOL-001 | tool_listing | MUST | `list_tools()` returns a list |
| TOOL-002 | tool_listing | MUST | Each tool has `name` and `inputSchema` fields |
| TOOL-003 | tool_listing | MUST | All tool names are unique |
| TOOL-004 | tool_listing | SHOULD | Each `inputSchema` has `type: "object"` at root |
| CALL-001 | tool_calling | MUST | Calling a valid tool with matching arguments returns result |
| CALL-002 | tool_calling | MUST | Result contains `content` list |
| CALL-003 | tool_calling | MUST | Successful result has `isError` absent or False |
| CALL-004 | tool_calling | MUST | Calling unknown tool name returns error |
| CALL-005 | tool_calling | SHOULD | Error response sets `isError` to True |
| ERR-001 | error_handling | MUST | Error result contains text content with message |
| ERR-002 | error_handling | SHOULD | Server handles empty arguments dict without crashing |
| ERR-003 | error_handling | SHOULD | Server handles None arguments without crashing |
| RES-001 | resources | MUST | `list_resources()` returns a list |
| RES-002 | resources | MUST | Each resource has `uri` and `name` fields |
| RES-003 | resources | MUST | Resource URIs are unique |

## Capture — tests write themselves

Point `mcptest capture` at any MCP server and it auto-discovers tools, samples responses,
and writes both fixture YAML and test-spec YAML.

```bash
mcptest capture "python my_server.py" --output fixtures/ --generate-tests
```

| Flag | Default | Description |
|------|---------|-------------|
| `--output` / `-o` | `.` | Directory where files are written |
| `--generate-tests` | off | Also write a test-spec YAML |
| `--samples-per-tool` | `3` | Argument variations tried per tool |
| `--dry-run` | off | Preview without writing files |
| `--agent` | `python agent.py` | Agent command embedded in test suites |

## Semantic evaluation

`mcptest eval` scores agent text output against named criteria — no LLM API calls required.

```yaml
# rubrics/booking.yaml
rubric:
  name: booking-quality
  criteria:
    - name: correctness
      weight: 0.5
      method: keywords
      expected: [confirmed, booking_id, receipt]
      threshold: 0.6
    - name: format
      weight: 0.3
      method: pattern
      expected: "Booking \\w+ confirmed"
      threshold: 1.0
    - name: completeness
      weight: 0.2
      method: similarity
      expected: "Your booking ABC123 is confirmed. You will receive a receipt."
      threshold: 0.7
```

```bash
mcptest eval tests/ --rubric rubrics/booking.yaml
mcptest eval tests/ --rubric rubrics/booking.yaml --ci --fail-under 0.75
```

Grading methods: `keywords`, `pattern`, `similarity`, `contains`, `custom`.

## Benchmarking

Run the same test suite against multiple agent profiles and produce a side-by-side comparison:

```yaml
# agents.yaml
agents:
  - name: claude-sonnet
    command: python agents/claude_agent.py
    env: { MODEL: claude-sonnet-4-20250514 }
  - name: gpt-4o
    command: python agents/openai_agent.py
    env: { MODEL: gpt-4o }
```

```bash
mcptest bench tests/ --agents agents.yaml
mcptest bench tests/ --agents agents.yaml --ci --fail-under 0.75
```

Output: leaderboard, metric comparison pivot table, per-test pass/fail grid.

## Configuration

Place a `mcptest.yaml` in your project root:

```yaml
test_paths: ["tests/"]
fixture_paths: ["fixtures/"]
baseline_dir: .mcptest/baselines

retry: 3
tolerance: 0.8
parallel: 4
fail_fast: false
fail_under: 0.0

thresholds:
  tool_efficiency: 0.7
  redundancy: 0.3

plugins:
  - my_company.mcptest_extensions
  - ./custom_assertions.py

cloud:
  url: https://mcptest.example.com
  api_key_env: MCPTEST_API_KEY
```

```bash
mcptest config  # inspect resolved configuration
```

## Cloud dashboard

A lightweight web UI for test analytics — Tailwind CSS + htmx + Chart.js, zero build step.

```bash
pip install 'mcp-agent-test[cloud]'
mcptest dashboard
```

| Page | Description |
|------|-------------|
| **Overview** | Stats cards, recent runs, per-suite pass/fail bars |
| **Runs** | Filterable, paginated run list with live htmx updates |
| **Run detail** | Metric charts, collapsible tool-call timeline, promote-as-baseline |
| **Trends** | Line charts of any metric over time with baseline markers |
| **Baselines** | Active baseline table, one-click demote, two-run comparison |
| **Webhooks** | Register endpoints, view delivery history, send test pings |

### Webhook events

| Event | Fires when |
|-------|-----------|
| `run.created` | A new test run is pushed |
| `regression.detected` | Metric regression found vs baseline |
| `baseline.promoted` | A run is promoted as baseline |
| `baseline.demoted` | A baseline is removed |

Deliveries are HMAC-SHA256 signed, retry up to 3x with exponential back-off,
and logged in the dashboard.

## Plugins

```yaml
# mcptest.yaml
plugins:
  - my_company.mcptest_extensions
  - ./custom_assertions.py
```

Or use `confmcptest.py` (auto-discovered like pytest's `conftest.py`):

```python
from mcptest.assertions.base import register_assertion, _AssertionBase, AssertionResult
from mcptest.runner.trace import Trace

@register_assertion
class response_is_json(_AssertionBase):
    yaml_key = "response_is_json"
    def check(self, trace: Trace) -> AssertionResult: ...
```

Or distribute via entry points:

```toml
[project.entry-points."mcptest.assertions"]
my_assertions = "my_package.assertions"
```

## CLI reference

```
mcptest run          Run test files
mcptest init         Scaffold a new project
mcptest capture      Auto-generate fixtures from a live server
mcptest validate     Validate YAML without running
mcptest snapshot     Save baselines
mcptest diff         Diff against baselines (--ci for CI gate)
mcptest watch        Auto-rerun on file changes
mcptest coverage     Fixture coverage report
mcptest conformance  MCP protocol conformance checks
mcptest eval         Semantic evaluation with rubrics
mcptest bench        Multi-agent benchmarking
mcptest scorecard    Weighted quality report card
mcptest metrics      Compute quality metrics from traces
mcptest compare      Compare two trace files
mcptest record       Record a single agent run
mcptest export       Convert results to JUnit/TAP/HTML
mcptest badge        Generate shields.io badge
mcptest config       Show resolved configuration
mcptest explain      Inline docs for any assertion/metric/check
mcptest docs         Generate MkDocs documentation site
mcptest generate     Generate test YAML from fixture schemas
mcptest list-packs   List built-in fixture packs
mcptest install-pack Install a fixture pack
mcptest cloud-push   Push traces to the cloud backend
mcptest dashboard    Launch the web UI
mcptest github-comment  Post PR summary comment
```

## Stats

- **1,801 tests** passing
- **93% code coverage**
- **29 CLI commands**
- **17 trajectory assertions**
- **7 quality metrics**
- **19 conformance checks**
- **6 pre-built fixture packs**
- Python 3.10+, MIT licensed, zero vendor lock-in

## License

MIT — see [LICENSE](LICENSE).
