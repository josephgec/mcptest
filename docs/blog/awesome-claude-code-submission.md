# awesome-claude-code submission

**IMPORTANT:** The `hesreallyhim/awesome-claude-code` repo explicitly states
**only Claude can submit PRs**. Humans submit via the issue template. Do not
fork + PR.

## Submission channel

1. Go to: https://github.com/hesreallyhim/awesome-claude-code/issues/new/choose
2. Pick the **"Recommend a new resource"** template.
3. Category: **Tooling 🧰** (applications built on Claude Code, not just
   configuration).
4. Paste the fields below.

## Fields to paste

**Resource name:**
```
mcptest
```

**GitHub URL:**
```
https://github.com/josephgec/mcptest
```

**One-line description (follow their existing Tooling entries for tone):**
```
pytest for MCP agents — YAML-mock MCP servers, assert on tool-call trajectories, and catch silent regressions from prompt or model changes with `mcptest diff --ci`. Ships with a Claude Code skill at `.claude/skills/mcptest/SKILL.md`.
```

**Longer description (if the template has a "details" field):**
```
`mcptest` is an MIT-licensed, vendor-neutral testing framework specifically
shaped to MCP agents. Install via `pip install mcp-agent-test` (the CLI is
`mcptest`).

Core features that are relevant to Claude Code users:
- Mock MCP servers from YAML — no code, speaks real MCP over stdio/SSE
- 17 trajectory assertions (tool_called, tool_order, param_matches,
  error_handled, metric_above, …) — test what the agent did, not just
  whether the code ran
- Regression diffing (`mcptest snapshot` → `mcptest diff --ci`) — the "I
  asked Claude Code to clean up the prompt and it silently dropped the
  duplicate-check step" class of bug becomes a failed CI job instead of a
  production incident
- 6 pre-built fixture packs (GitHub, Slack, filesystem, database, HTTP, git)
  so `mcptest install-pack github . && mcptest run` is green in under a
  minute
- MCP server conformance checks (19 checks across 5 sections) for server
  authors
- Ships a Claude Code skill at `.claude/skills/mcptest/SKILL.md` that
  auto-activates when the user asks to test an MCP agent

Why Claude Code users specifically benefit: Claude Code can generate MCP
agent code fast, but has no native way to verify that a subsequent refactor,
prompt tweak, or model swap didn't silently change agent behavior. mcptest
closes that loop with a hermetic, deterministic, tokens-free test harness.
```

## Submission checklist

- [ ] v0.1.0 published to PyPI ✓ (done)
- [ ] GitHub repo public with README ✓ (done)
- [ ] `.claude/skills/mcptest/SKILL.md` exists in the repo ✓ (done in this PR)
- [ ] CLAUDE.md at repo root ✓ (done in this PR)
- [ ] At least one blog post live (not just in-repo markdown) — ideally
      Dev.to — **you need to publish this first** so the submission points
      at a post the curator can read without cloning the repo
- [ ] Wait ~24-48h after the Dev.to post goes live before submitting, so
      the listing has social proof (star count, some engagement)

## After submission

If accepted, the awesome-list listing drives GitHub → repo → PyPI traffic.
Track the star delta over the first 7 days to gauge impact. Most approved
entries land in a batched edit-by-Claude PR within a few days.

If you don't hear back in a week, it's fine to reply-bump on the issue.
