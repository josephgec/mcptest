# I asked Claude Code to build an MCP agent. Here's how I test what it ships.

*If you let Claude Code generate an MCP agent end-to-end, you will have
working code in minutes. Whether it does what you meant it to do is a
different question — and the one nobody's answering.*

---

Two Fridays ago I asked Claude Code to build me a GitHub issue triage agent.
Forty minutes later I had a working Python MCP agent, a `.mcp.json` config, a
README, and a set of tools the agent could call against the real GitHub MCP
server. I clicked approve on maybe eight tool calls and it was done.

Monday morning my team filed duplicate issues all day.

The agent ran fine. No stack traces. No errors in the logs. It just stopped
calling `list_issues` before `create_issue` — a detail of the original prompt
Claude Code had wired up that got dropped in one of my subsequent
"can you clean this up a bit" edits. Unit tests passed. Linter passed. CI went
green and shipped to production. There was no test that said "check for
duplicates before filing" — because the original behavior emerged from the
system prompt, not from code, and my tests tested code.

This is the shape of nearly every Claude Code agent bug I've seen.

## What Claude Code ships well

Generating the *code* of an MCP agent is the part that's gotten cheap. Claude
Code is genuinely good at:

- Wiring up `mcp.ClientSession` + stdio transport
- Translating a prose requirement into a system prompt + tool loop
- Adding error handling and retry logic
- Writing a reasonable `.mcp.json` with the right transport stanza
- Producing the supporting CLI, README, and argparse scaffolding

All of that is real engineering work Claude Code does well. The code runs,
the types check, the imports resolve.

## What Claude Code can't ship

The part that doesn't get generated: **a test that asserts on the agent's
behavior, not its code.**

- Does the agent call `list_issues` before `create_issue`?
- Does it retry at most twice on rate-limit errors, not 50 times?
- Does it *never* call `delete_issue`, under any input?
- If I change the system prompt to be shorter, does it still do all of the
  above?

These aren't bugs the code itself can have. They're bugs the *composition* of
code + prompt + model + MCP server exhibits. You can't catch them with pytest.
You can't catch them with a linter. You can't catch them with a generic LLM
eval. You catch them by running the agent against a hermetic mock of the MCP
server and asserting on the sequence of tool calls.

That's what `mcptest` does. It exists because I got tired of the "Claude Code
shipped the agent in 40 minutes, bug landed in production over the weekend"
cycle.

## The workflow I use now

Claude Code generates the agent. I add four files.

**1. A fixture YAML that mocks the MCP server the agent talks to:**

```yaml
# fixtures/github.yaml
server: { name: mock-github, version: "1.0" }

tools:
  - name: list_issues
    input_schema:
      type: object
      properties: { repo: { type: string }, query: { type: string } }
      required: [repo]
    responses:
      - match: { query: "login 500" }
        return: { issues: [] }                 # dup check returns empty → create
      - match: { query: "dark mode" }
        return: { issues: [{ number: 12 }] }    # dup found → agent should comment

  - name: create_issue
    responses:
      - match: { repo: "acme/api" }
        return: { number: 42 }
      - default: true
        error: rate_limited

  - name: delete_issue
    responses:
      - default: true
        return: { deleted: true }

errors:
  - name: rate_limited
    error_code: -32000
    message: "GitHub API rate limit exceeded"
```

This is a *real* MCP server. It speaks MCP over stdio. The agent Claude Code
generated connects to it exactly the same way it connects to the real GitHub
MCP server. No code changes.

**2. A test YAML asserting on behavior:**

```yaml
name: triage agent
fixtures: [../fixtures/github.yaml]
agent:
  command: python agent.py       # the agent Claude Code just wrote

cases:
  - name: checks duplicates before filing
    input: "login page returns 500 on Safari"
    assertions:
      - tool_order: [list_issues, create_issue]

  - name: never calls delete
    input: "buy crypto NOW 🚀🚀🚀"
    assertions:
      - tool_not_called: delete_issue

  - name: recovers from rate-limit
    input: "file a bug in wrongorg/wrongrepo"
    assertions:
      - tool_called: create_issue
      - error_handled: "rate limit"

  - name: comments on duplicates instead of filing
    input: "add dark mode support"
    assertions:
      - tool_called: add_comment
      - tool_not_called: create_issue
```

**3. Run it:**

```
$ mcptest run

                        mcptest results
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Suite          ┃ Case                               ┃ Status ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ triage agent   │ checks duplicates before filing    │  PASS  │
│ triage agent   │ never calls delete                 │  PASS  │
│ triage agent   │ recovers from rate-limit           │  PASS  │
│ triage agent   │ comments on duplicates             │  PASS  │
└────────────────┴────────────────────────────────────┴────────┘

4 passed, 0 failed (4 total)
⏱  1.4s
```

1.4 seconds. Zero tokens. No real GitHub API calls.

**4. Freeze the baseline:**

```bash
mcptest snapshot
git add .mcptest/baselines
git commit -m "freeze agent trajectories"
```

Now every future change Claude Code makes to the agent gets diffed against this
baseline before it can merge.

## The regression CI is the thing

I asked Claude Code to "clean up the system prompt, it's too verbose." It
produced a diff that looked harmless. Everything compiled. All unit tests
passed.

```
$ mcptest diff --ci

✗ triage agent::checks duplicates before filing
  tool_order REGRESSION:
    baseline: list_issues → create_issue
    current:  create_issue

✗ triage agent::comments on duplicates
  tool_called REGRESSION:
    baseline: add_comment was called
    current:  add_comment was never called

2 regression(s) across 4 case(s)
Exit: 1
```

Claude Code's "cleanup" had dropped "ALWAYS check list_issues for duplicates"
from the system prompt. The diff output told me exactly which behavior was
gone. I restored the clause, pushed again, diff went green.

This is the cycle I want with AI coding tools: let them ship the work, and
have a trajectory-level safety net that makes it safe to accept their changes.

## Making it a Claude Code skill

If you're using Claude Code heavily, add mcptest as a project skill so Claude
auto-discovers it:

```
your-project/
  .claude/
    skills/
      mcptest/
        SKILL.md          # from github.com/josephgec/mcptest
```

Next time you tell Claude Code "test this agent", it finds the skill,
scaffolds a fixture and test case, and runs `mcptest`. You review the output,
not the boilerplate.

A `CLAUDE.md` at your repo root pointing at `fixtures/`, `tests/`, and the
mcptest workflow means every Claude Code session on your project knows how to
run the right checks before declaring the work done.

## Setup in 60 seconds

```bash
pip install mcp-agent-test

# Scaffold
mcptest init

# Or grab a pre-built pack for the MCP server your agent uses
mcptest install-pack github .    # or slack, filesystem, database, http, git

# Run, watch the green table
mcptest run

# Freeze the trajectories
mcptest snapshot && git add .mcptest/baselines
```

Then drop this workflow in `.github/workflows/agent-ci.yml` and every PR
Claude Code (or anyone) opens against the agent gets trajectory-level
regression gating:

```yaml
- run: pip install mcp-agent-test
- run: mcptest run
- run: mcptest diff --ci
```

## The broader point

Claude Code will keep getting better at generating agent code. The bottleneck
for anyone shipping production agents stops being *can I write this?* and
becomes *can I verify this still does what I meant it to do, three refactors
later?*

Code-level testing answers the first question. Trajectory-level testing
answers the second. You need both.

---

**Source:** [github.com/josephgec/mcptest](https://github.com/josephgec/mcptest) · **PyPI:** [mcp-agent-test](https://pypi.org/project/mcp-agent-test/) · **Skill file:** [.claude/skills/mcptest/SKILL.md](https://github.com/josephgec/mcptest/blob/main/.claude/skills/mcptest/SKILL.md)

*If this was useful and you're building MCP agents with Claude Code, a ⭐ on
the repo helps other Claude Code users find it.*
