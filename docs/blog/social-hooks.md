# Social distribution hooks

Copy-paste-ready copy for each platform. Post the blog post first (Dev.to or similar), then use the canonical URL in everything below.

Placeholder: `{BLOG_URL}` = your Dev.to / canonical URL.

---

## Twitter / X (280 chars)

**Hook 1 — the pain**
```
I kept silently breaking my MCP agent with one-line prompt changes.
Unit tests passed. Lint passed. CI went green. Production broke.

Wrote a tool that diffs agent trajectories against a baseline and fails CI when behavior changes.

Post: {BLOG_URL}
```

**Hook 2 — the feature**
```
pytest for MCP agents.

• mock MCP servers from YAML
• assert which tools got called in what order
• diff trajectories against a baseline → CI fails on prompt-change regressions
• 6 pre-built fixture packs (GitHub, Slack, FS, DB, HTTP, git)

pip install mcp-agent-test

{BLOG_URL}
```

**Hook 3 — the numbers**
```
5 test cases. 1.6 seconds. Zero tokens spent.

Hermetic testing for MCP agents — mocks speak real MCP over stdio, agent connects the same way it connects to production.

pip install mcp-agent-test

{BLOG_URL}
```

---

## Bluesky (300 chars, friendlier tone)

```
Built a "pytest for MCP agents". You mock MCP servers in YAML, assert on tool-call order, and snapshot trajectories so CI fails when a prompt change silently alters agent behavior.

Wrote up a walkthrough with 5 real test cases: {BLOG_URL}

Open source, MIT, independent.
```

---

## LinkedIn (longer, story format)

```
Three bugs that convinced me AI agent testing is broken:

1) Tweaked a system prompt. Agent stopped filing GitHub issues, just summarised in text. All my tests passed — they tested code, not behavior.

2) Swapped Sonnet → Haiku to save cost. Agent started calling list_issues four times per create_issue. Integration tests green. Token bill tripled.

3) Real GitHub API rate-limited me mid-CI. Couldn't tell flake from regression. Rolled back a perfectly good PR.

Every one of these is a trajectory-level bug. Unit tests can't see them. Generic LLM-eval tools don't understand MCP semantics.

So I wrote mcptest — pytest for MCP agents. YAML mocks, trajectory assertions, regression diffing, GitHub Action. Open-source (MIT), no vendor lock-in.

Walkthrough with 5 real test cases: {BLOG_URL}

pip install mcp-agent-test
```

---

## MCP Discord / r/MCP / r/LocalLLaMA (community tone)

**Title:** `Open-sourced a "pytest for MCP agents" — looking for early feedback`

```
Hey all — I've been building MCP agents for a few months and kept hitting the same problem: no hermetic way to test them. Every framework (DeepEval, Inspect AI, etc.) tests prompts or code, not the trajectory (which tool got called, in what order, with what args).

So I wrote mcptest. Key pieces:

• Mock MCP servers from a YAML fixture — speaks real MCP over stdio/SSE, so your agent connects the same way it would to a production server.
• 17 trajectory assertions (tool_called, tool_order, param_matches, error_handled, no_errors, max_tool_calls, metric_above, etc.)
• Regression diffing — mcptest snapshot → change your prompt → mcptest diff --ci catches trajectory drift before merge.
• 6 pre-built fixture packs (GitHub, Slack, filesystem, database, HTTP, git). `mcptest install-pack github .` → `mcptest run` → green.
• GitHub Action for PR-level regression gating.
• MIT, independent, no vendor account required.

Writeup with a concrete scenario (5 test cases for a GitHub triage agent): {BLOG_URL}

PyPI: https://pypi.org/project/mcp-agent-test/
Source: https://github.com/josephgec/mcptest

Looking for feedback from anyone shipping real MCP agents — especially the trajectory-assertion surface. Which primitives are missing? What's painful? Happy to add packs for specific MCP servers people are using.
```

---

## Hacker News (Show HN)

**Title:** `Show HN: mcptest – pytest for MCP agents (mock servers, trajectory diffs)`

**Body:**
```
Hi HN — I've been building MCP agents recently and ran into a testing gap: there's no clean way to run an agent against a mocked MCP server and assert on its tool-call trajectory. So I wrote mcptest.

Three things made this worth building:

1. Every competitor that tried to do generic LLM eval (Promptfoo, Humanloop, Galileo) got absorbed into platform companies. The independent, vendor-neutral tier is thin.

2. MCP agents are trajectory-level things — the bugs are "dropped the list_issues call before create_issue" or "retry-stormed when rate-limited." Generic prompt eval can't see those.

3. The MCP SDKs handle the protocol plumbing, so building a mock server that speaks real MCP is actually cheap — you just need a declarative fixture format + a runner + assertions.

What's shipped (v0.1.0):

- YAML mock servers (stdio + SSE/HTTP)
- 17 trajectory assertions, 7 quality metrics, 19 MCP conformance checks
- Regression diffing with CI gating (exit 1 on trajectory drift)
- 6 pre-built fixture packs for popular MCP server patterns
- GitHub Action + PR comment bot
- Optional FastAPI cloud backend with web dashboard + webhooks
- pytest plugin for users who prefer Python tests over YAML
- 1800 tests, 93% coverage, MIT licensed

Walkthrough with 5 test cases on a GitHub triage agent: {BLOG_URL}

pip install mcp-agent-test

Would love feedback on the assertion surface and which fixture packs to build next. The current packs are reasonable smoke tests but I'd like to turn them into realistic integration suites for the actual MCP servers people use (GitHub MCP, Zed, etc.).
```

---

## GitHub Discussion on official MCP SDK repos

**Title:** `Testing framework for MCP agents — would love feedback`

**Body:**
```
Hi — I built mcptest, a framework for testing MCP agents with mocked servers, trajectory assertions, and regression diffing. It uses the official MCP Python SDK for the protocol plumbing; the rest is YAML fixture authoring + a runner + assertion library + a CLI.

Sharing here because (a) I'd love feedback from folks who've actually built MCP agents, and (b) some of you are clearly hitting the same "how do I put this in CI without burning tokens on every test run" problem.

Walkthrough: {BLOG_URL}
Source: https://github.com/josephgec/mcptest
PyPI: https://pypi.org/project/mcp-agent-test/

Specifically interested in:

- What trajectory assertions would you want that mcptest doesn't have yet?
- Which real MCP servers should I ship as fixture packs (beyond the current 6: GitHub, Slack, filesystem, database, HTTP, git)?
- Any DX friction in the YAML fixture format that pushes you toward writing tests in Python instead?

Happy to answer questions.
```

---

## Publishing order (recommendation)

1. **Dev.to** first (pastes natively with frontmatter, tags work, SEO is decent). This becomes your canonical URL.
2. **Hashnode and Medium** second, both with `canonical_url` pointing at Dev.to (so Google doesn't split ranking).
3. **LinkedIn** on day 1 as well — different audience, not cannibalising.
4. **Twitter / Bluesky** on day 1, morning, as the first "launch" post. Rotate the three hooks across a few days.
5. **Show HN** on day 2 or 3 (after you've got a couple of stars and some blog traction). HN moderators notice low-effort / no-traction submissions and rank them down.
6. **Reddit (r/MCP, r/LocalLLaMA)** and **MCP Discord** on day 2-3. Don't drop a link cold — reply to existing threads where people are complaining about testing, and link with context.
7. **GitHub Discussion** on the official MCP Python SDK repo on day 3-5. Frame it as "I built this on top of your SDK, would love feedback."

Do not post to more than 2 platforms in a single hour — each one is a distinct conversation; spread your attention.
