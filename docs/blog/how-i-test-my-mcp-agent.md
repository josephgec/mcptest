# How I test my MCP agent without burning tokens

*A 10-minute walkthrough: mock a flaky GitHub API, assert on tool-call order,
catch a silent regression after a prompt change.*

---

Last month I shipped an MCP agent that triages GitHub issues. It works fine
until it doesn't. The last three bugs I found in it:

1. I tweaked the system prompt. The agent stopped calling `create_issue` and
   started just summarising the bug report in text. My CI didn't notice — my CI
   ran the *code*, not the *agent behavior*.
2. I swapped from Sonnet to Haiku to save cost. The agent started calling
   `list_issues` four times in a row before each `create_issue`. Still passed
   integration tests. Token bill tripled.
3. GitHub's real API rate-limited me mid-test run. My pytest CI marked the
   whole suite as failed. I rolled back a perfectly good change because I
   couldn't tell flake from regression.

Every one of those would have been caught by a tool that tested the *agent's
trajectory* — which tools it picked, in what order, with what arguments —
against a fast, hermetic mock. That tool is
[`mcptest`](https://github.com/josephgec/mcptest). Here's how I use it.

## The scenario

I have an agent that reads a bug report and decides whether to:
- Open a new issue (if the bug is novel)
- Comment on an existing issue (if a similar one is already filed)
- Do nothing (if the report is spam or unclear)

It talks to a GitHub MCP server. I want to test four things:

1. For a clear bug report, it calls `create_issue` exactly once.
2. It checks `list_issues` **before** `create_issue` to avoid duplicates.
3. When the server rate-limits it, it recovers — doesn't crash or retry-storm.
4. It doesn't accidentally call `delete_issue` on anything. Ever.

## Step 1: mock the GitHub MCP server in YAML

No code. Just declare the tools, the canned responses, and the error scenarios.

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
        return:
          issues: []                   # no duplicate — should open a new one
      - match: { query: "dark mode" }
        return:
          issues: [{ number: 12, title: "Add dark mode" }]  # duplicate — should comment
      - default: true
        return: { issues: [] }

  - name: create_issue
    input_schema:
      type: object
      properties:
        repo: { type: string }
        title: { type: string }
        body: { type: string }
      required: [repo, title]
    responses:
      - match: { repo: "acme/api" }
        return: { number: 42, url: "https://github.com/acme/api/issues/42" }
      - default: true
        error: rate_limited            # simulate real-world flake

  - name: add_comment
    input_schema:
      type: object
      properties:
        repo: { type: string }
        number: { type: integer }
        body: { type: string }
      required: [repo, number, body]
    responses:
      - default: true
        return: { ok: true }

  - name: delete_issue
    responses:
      - default: true
        return: { deleted: true }

errors:
  - name: rate_limited
    error_code: -32000
    message: "GitHub API rate limit exceeded"
```

That's a real MCP server. It speaks MCP over stdio, just like the real one.
My agent connects to it the same way.

## Step 2: write the tests

```yaml
# tests/test_triage.yaml
name: triage agent
fixtures:
  - ../fixtures/github.yaml
agent:
  command: python agent.py         # your agent — any language, any SDK
cases:

  - name: opens a new issue for a novel bug
    input: "login page returns 500 on Safari"
    assertions:
      - tool_called: create_issue
      - tool_call_count: { tool: create_issue, count: 1 }
      - param_matches:
          tool: create_issue
          param: repo
          value: "acme/api"
      - no_errors: true

  - name: checks duplicates before creating
    input: "login page returns 500 on Safari"
    assertions:
      - tool_order:
          - list_issues
          - create_issue

  - name: comments on an existing duplicate instead of creating
    input: "add dark mode support"
    assertions:
      - tool_called: add_comment
      - tool_not_called: create_issue

  - name: never deletes anything
    input: "spam: buy crypto now!!!"
    assertions:
      - tool_not_called: delete_issue

  - name: recovers from rate-limit gracefully
    input: "file bug for org: wrongorg/wrongrepo"  # triggers default rate_limited
    assertions:
      - tool_called: create_issue
      - error_handled: "rate limit"
```

Five test cases. Every one of them maps directly to a bug I've actually hit.

## Step 3: run it

```bash
$ mcptest run

                        mcptest results
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Suite          ┃ Case                               ┃ Status ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ triage agent   │ opens a new issue for a novel bug  │  PASS  │
│ triage agent   │ checks duplicates before creating  │  PASS  │
│ triage agent   │ comments on existing duplicate     │  PASS  │
│ triage agent   │ never deletes anything             │  PASS  │
│ triage agent   │ recovers from rate-limit gracefully│  PASS  │
└────────────────┴────────────────────────────────────┴────────┘

5 passed, 0 failed (5 total)
⏱  1.6s
```

1.6 seconds. Zero tokens. No GitHub API calls. No rate-limit flake.

That's the whole quickstart. But the real value comes next.

## Step 4: the regression-diff trick

Here's where the tool earns its keep.

I snapshot the current (known-good) agent trajectories as baselines:

```bash
$ mcptest snapshot
✓ saved baseline for triage agent::opens a new issue... (2 tool call(s))
✓ saved baseline for triage agent::checks duplicates... (2 tool call(s))
✓ saved baseline for triage agent::comments on duplicate (2 tool call(s))
...
```

Now I go tweak the system prompt. Something innocuous. I change:

> "You are a GitHub issue triage assistant. Check for duplicates before filing."

To:

> "You are a helpful assistant that handles GitHub issue reports."

No `[ERROR]` in my code. All my unit tests still pass. My linter is happy.

```bash
$ mcptest diff --ci

✗ triage agent::checks duplicates before creating
  tool_order REGRESSION:
    baseline: list_issues → create_issue
    current:  create_issue          ← list_issues was dropped!

✗ triage agent::comments on existing duplicate
  tool_called REGRESSION:
    baseline: add_comment was called
    current:  add_comment was never called

2 regression(s) across 5 case(s)
Exit: 1
```

Exit code 1 — CI blocks the merge. The agent **silently** lost its duplicate-
check behavior because of a one-sentence prompt change.

This is the bug that cost me a weekend last year. I never want to hit it again.

## Step 5: wire it into CI

```yaml
# .github/workflows/agent-tests.yml
name: Agent tests
on: [pull_request]

jobs:
  mcptest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }

      - run: pip install mcp-agent-test

      - name: Run tests
        run: mcptest run

      - name: Diff against baselines
        run: mcptest diff --ci

      - name: Post PR summary
        if: always()
        run: mcptest github-comment
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Now every PR that changes the prompt, the model, the agent code, or the MCP
server gets its trajectories diffed against main. If behavior changes, a
comment lands on the PR with the exact tool-order delta. Reviewers see
*agent behavior changed* as clearly as they see *code changed*.

## Why this matters right now

Three independent eval tools got acquired in the last twelve months:
Promptfoo by OpenAI, Humanloop by Anthropic, Galileo by Cisco. Every one of
them left behind teams wondering whether to stay on a platform-owned tool or
switch. `mcptest` is independent, MIT-licensed, and specifically shaped to
MCP agents — the `tool_called` / `tool_order` / `error_handled` primitives
exist because that's what an MCP trajectory actually looks like, not because
someone ported a generic LLM-eval DSL.

And MCP agent testing is *particularly* underserved. DeepEval is great for
prompt evaluation. Inspect AI is great for benchmarks. Neither gives you
"run your agent against a mock GitHub server and assert it didn't call
`delete_issue`." `mcptest` does.

## Try it

```bash
pip install mcp-agent-test

# Scaffold a new project
mcptest init

# Or clone the 60-second quickstart
git clone https://github.com/josephgec/mcptest
cd mcptest/examples/quickstart
mcptest run

# Or install a pre-built fixture pack for a popular MCP server
mcptest install-pack github ./my-project
mcptest install-pack slack ./my-project
mcptest install-pack filesystem ./my-project
```

Six packs ship out of the box — GitHub, Slack, filesystem, database, HTTP, git.
Each one is a realistic mock with error scenarios baked in and tests that
actually assert something. `mcptest install-pack github ./` → `mcptest run`
→ green output in under a minute.

Source: <https://github.com/josephgec/mcptest>
PyPI: <https://pypi.org/project/mcp-agent-test/>

---

*If you're building an MCP agent and haven't started writing tests yet, you're
accumulating the same three bugs I accumulated. Start with one fixture and
one test case. Catch the first prompt-change regression. Then you'll
understand why MCP agents need this tool.*
