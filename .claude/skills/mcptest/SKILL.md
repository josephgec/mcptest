---
name: mcptest
description: Use when the user wants to test, debug, or CI-gate an MCP (Model Context Protocol) agent — writing fixtures that mock MCP servers, asserting on tool-call trajectories, snapshotting behavior to catch prompt/model regressions, or installing pre-built fixture packs for GitHub/Slack/filesystem/database/HTTP/git. Trigger phrases include "test my MCP agent", "mock an MCP server", "catch agent regressions", "why did my agent stop calling X tool", "CI for my agent", "how do I test tool-call order", "pytest for MCP". Installs via `pip install mcp-agent-test`; CLI is `mcptest`.
when_to_use: The user is building or maintaining an MCP agent and wants fast, hermetic tests (no tokens, no real API calls) and/or wants to detect when a prompt tweak, model swap, or MCP-server change silently altered agent behavior.
---

# mcptest — testing framework for MCP agents

mcptest is the "pytest for MCP agents": YAML-declared mock MCP servers, 17 trajectory
assertions, and a snapshot+diff flow that turns silent prompt regressions into failed CI
jobs. This skill teaches you how to help the user get to green in under a minute, then
wire it into CI.

**Install (PyPI name differs from import name):**
```bash
pip install mcp-agent-test    # CLI/import is still `mcptest`
```

## Step 1 — pick the fastest on-ramp

Before writing anything custom, check whether an existing pack fits. mcptest ships six
realistic packs with error scenarios baked in:

```bash
mcptest list-packs
```

Output: `database`, `filesystem`, `git`, `github`, `http`, `slack`. If the user's agent
talks to any of these, install the pack and start there:

```bash
mcptest install-pack github ./my-project
cd my-project && mcptest run   # should be green in under a minute
```

If none fits, proceed to step 2.

## Step 2 — scaffold a project

```bash
mcptest init ./my-project
cd my-project
```

This creates:
- `fixtures/example.yaml` — mock MCP server definition
- `tests/test_example.yaml` — test suite with assertions
- `examples/example_agent.py` — toy scripted agent (replace with real one)

## Step 3 — write a fixture (mock MCP server)

Fixtures are pure YAML. They declare tools, their responses per-argument-match, and
named error scenarios.

```yaml
# fixtures/github.yaml
server:
  name: mock-github
  version: "1.0"

tools:
  - name: list_issues
    input_schema:
      type: object
      properties:
        repo: { type: string }
        query: { type: string }
      required: [repo]
    responses:
      - match: { query: "login 500" }
        return: { issues: [] }               # no dup → agent should create
      - match: { query: "dark mode" }
        return: { issues: [{ number: 12 }] } # dup → agent should comment
      - default: true
        return: { issues: [] }

  - name: create_issue
    input_schema:
      type: object
      properties: { repo: { type: string }, title: { type: string } }
      required: [repo, title]
    responses:
      - match: { repo: "acme/api" }
        return: { number: 42 }
      - default: true
        error: rate_limited

errors:
  - name: rate_limited
    error_code: -32000
    message: "GitHub API rate limit exceeded"
```

Response matching rules (in order):
1. `match: {k: v}` — exact arg match
2. `match_regex: {k: pattern}` — regex arg match
3. `default: true` — fallback (must be last)

Either `return: {...}` (structured JSON), `return_text: "..."` (plain text),
or `error: <error_name>` (must be declared in the `errors:` block).

## Step 4 — write test cases (trajectory assertions)

```yaml
# tests/test_triage.yaml
name: triage agent
fixtures:
  - ../fixtures/github.yaml
agent:
  command: python agent.py       # the user's agent, any language
  timeout_s: 10
cases:
  - name: checks duplicates before creating
    input: "login page returns 500 on Safari"
    assertions:
      - tool_order: [list_issues, create_issue]

  - name: recovers from rate-limit
    input: "file bug in wrongorg/wrongrepo"
    assertions:
      - tool_called: create_issue
      - error_handled: "rate limit"   # substring of the error MESSAGE, not name
```

**The 17 trajectory assertions** (all live in `mcptest.assertions.impls` and
`mcptest.assertions.combinators`; run `mcptest explain <name>` for any of them):

| Assertion | What it checks |
|-----------|----------------|
| `tool_called: <name>` | Tool was called at least once |
| `tool_not_called: <name>` | Tool was never called |
| `tool_call_count: {tool, count}` | Exact number of calls |
| `max_tool_calls: <n>` | Total calls across all tools ≤ n |
| `param_matches: {tool, param, value \| contains \| regex}` | Specific arg value match |
| `param_schema_valid` | All calls passed the tool's JSON Schema |
| `tool_order: [a, b, c]` | Tools called in this sequence (contiguous not required) |
| `trajectory_matches: [...]` | Full call list matches exactly |
| `completes_within_s: <n>` | Total trace latency ≤ n seconds |
| `output_contains: <str>` | Agent stdout contains substring |
| `output_matches: <regex>` | Agent stdout matches regex |
| `no_errors: true` | Zero tool calls errored |
| `error_handled: <msg_substring>` | Named error raised AND agent exit 0 |
| `metric_above: {metric, threshold}` | Quality metric ≥ threshold (see metrics below) |
| `metric_below: {metric, threshold}` | Quality metric ≤ threshold |
| `all_of`, `any_of`, `none_of` | Boolean combinators |
| `weighted_score: {threshold, weights}` | Composite metric score gate |

**Gotcha:** `error_handled` substring-matches against the error *message*, not the error
*name*. For a fixture error declared as `{name: path_traversal, message: "Path traversal blocked"}`,
write `error_handled: "Path traversal"`, not `error_handled: path_traversal`.

**Quality metrics available for `metric_above`/`metric_below`:**
`tool_efficiency`, `redundancy`, `schema_compliance`, `stability`, `tool_coverage`,
`error_recovery_rate`, `trajectory_similarity`.

## Step 5 — run it

```bash
mcptest run                    # default: tests/
mcptest run -j 4               # 4-way parallel
mcptest run --retry 5 --tolerance 0.8   # non-deterministic agents
mcptest run --json > out.json  # machine-readable
```

Rich table shows PASS/FAIL per case with assertion details. If anything fails, the
details column tells you exactly which assertion tripped.

## Step 6 — snapshot + diff (the regression-gate feature)

This is the feature mcptest exists for. After tests go green, freeze the trajectories:

```bash
mcptest snapshot
```

Baselines land in `.mcptest/baselines/<suite>__<case>.json`. Commit them:

```bash
git add .mcptest/baselines && git commit -m "freeze mcptest baselines"
```

Later, after any change that *could* alter agent behavior (prompt edit, model swap,
new tools in the MCP server, refactor), diff against the frozen trajectories:

```bash
mcptest diff --ci          # exits non-zero if any regression found
mcptest diff --ci --latency-threshold-pct 50   # also flag 50%+ slowdowns
```

Output shows the exact tool-order delta, missing calls, added calls, and per-metric
regressions. This is what catches the "one-line prompt change that silently drops the
`list_issues` duplicate check" class of bug.

## Step 7 — CI integration

Drop this into `.github/workflows/agent-ci.yml`:

```yaml
name: Agent CI
on:
  pull_request:
    paths: ["agent/**", "fixtures/**", "tests/**", "pyproject.toml"]
jobs:
  mcptest:
    runs-on: ubuntu-latest
    permissions: { contents: read, pull-requests: write }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install mcp-agent-test
      - run: mcptest run
      - run: mcptest diff --ci
      - if: always()
        run: mcptest github-comment
        env: { GITHUB_TOKEN: "${{ secrets.GITHUB_TOKEN }}" }
```

The PR comment shows regressions inline with a diff, metric deltas, and the command
to update baselines if the change was intentional.

## Other commands the user might ask about

| Command | Purpose |
|---------|---------|
| `mcptest capture "python server.py" --generate-tests` | Auto-discover tools from a live MCP server and write fixture + tests |
| `mcptest watch` | File-watching rerun loop (like `pytest-watch`) |
| `mcptest conformance --fixture fx.yaml` | 19 MCP protocol compliance checks for server authors |
| `mcptest bench tests/ --agents agents.yaml` | Side-by-side agent comparison (Claude vs GPT, Sonnet vs Haiku, etc.) |
| `mcptest eval --rubric rubric.yaml` | Semantic grading (keywords/regex/similarity), no LLM API calls |
| `mcptest scorecard trace.json` | Weighted quality report |
| `mcptest coverage --threshold 0.8` | Fixture surface-area coverage gate |
| `mcptest explain <name>` | Inline docs for any assertion, metric, or conformance check |

## Debugging tips for the agent that's talking to you

- **"My pack says 'no files matched pattern'"** — test YAML fixture refs must be
  `../fixtures/foo.yaml` when the layout is `{fixtures/, tests/}` as siblings.
- **"error_handled never matches"** — the assertion searches the error *message*
  (e.g. `"Path traversal blocked"`), not the error *name* (e.g. `path_traversal`).
- **"My agent hangs"** — check `agent.timeout_s` in the test YAML; default is 60s.
- **"Coverage dropped"** — `src/mcptest/__init__.py` and `src/mcptest/pytest_plugin.py`
  are omitted from coverage by design; check the rest.
- **"1801 tests but only 1790 now"** — some tests are parameterized by pack name in
  `tests/test_registry.py`; adding/removing a pack changes the count.

## When NOT to use this skill

- The user wants to test *non*-MCP agents (plain LangChain/OpenAI function-calling).
  mcptest is MCP-specific; suggest DeepEval or Inspect AI instead.
- The user wants LLM-graded evaluation with an external API (mcptest's `eval` is
  deterministic keyword/regex/similarity, not model-graded).
- The user is testing a single tool *implementation* rather than an agent's tool
  *choices*. Use unit tests for the tool, use mcptest for the agent that calls it.

## Links

- PyPI: https://pypi.org/project/mcp-agent-test/
- Source: https://github.com/josephgec/mcptest
- Blog walkthrough: https://github.com/josephgec/mcptest/blob/main/docs/blog/how-i-test-my-mcp-agent.md
- CI gate example: https://github.com/josephgec/mcptest/blob/main/docs/blog/ci-gate-example.md
