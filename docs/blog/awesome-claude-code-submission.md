# awesome-claude-code submission

**Do not submit before 2026-04-22.** Their template checklist requires "over
one week since the first public commit" and the repo's first commit is
2026-04-14. An earlier submission will fail automated validation.

## Submission channel

1. Go to: https://github.com/hesreallyhim/awesome-claude-code/issues/new/choose
2. Pick the resource-submission template (the one that produces a
   `resource-submission, validation-passed` labeled issue — look at
   [issue #1580](https://github.com/hesreallyhim/awesome-claude-code/issues/1580)
   for a recent accepted example).
3. Fill in the fields below verbatim.

## Fields (verified from a live 2026-04-15 accepted submission)

**Display Name**
```
mcptest
```

**Category**
```
Tooling
```

**Sub-Category**
```
General
```

**Primary Link**
```
https://github.com/josephgec/mcptest
```

**Author Name**
```
Joseph Thomas
```
(or whatever handle you want attributed)

**Author Link**
```
https://github.com/josephgec
```

**License**
```
MIT
```

**Other License**
```
(leave blank / No response)
```

**Description**
```
pytest for MCP agents — YAML-mock MCP servers, 17 trajectory assertions (tool_called, tool_order, param_matches, error_handled, metric_above, …), and a snapshot+diff flow that turns silent prompt-change regressions into failed CI jobs. Ships with a project Claude Code skill at `.claude/skills/mcptest/SKILL.md` that auto-activates when a user asks to test an MCP agent, plus six pre-built fixture packs (GitHub, Slack, filesystem, database, HTTP, git) so `mcptest install-pack github . && mcptest run` is green in under a minute. Independent, vendor-neutral, MIT-licensed.
```

**Validate Claims** *(how a reviewer can confirm the tool works)*
```
pip install mcp-agent-test
mcptest install-pack github /tmp/mcptest-check
cd /tmp/mcptest-check && mcptest run

# Expected: a Rich table with 5 green PASS rows, "5 passed, 0 failed (5 total)" in ~3s.
# Then verify the regression-diff loop:
mcptest snapshot
mcptest diff --ci   # exits 0 when nothing changed
```

**Specific Task(s)**
```
"Test my MCP agent. The agent code is in agent.py, it talks to a GitHub MCP server, and I want to assert it always calls list_issues before create_issue and never calls delete_issue."
```

**Specific Prompt(s)** *(a prompt a Claude Code user can paste to activate the skill)*
```
Please set up mcptest in this project. Install a github fixture pack, write a test case that asserts my agent calls list_issues before create_issue, and snapshot the current trajectories so we catch regressions on future PRs.
```

**Additional Comments** *(optional — use to flag the Claude Code-specific story)*
```
Built specifically for Claude Code users who ship MCP agents: the repo includes a SKILL.md that auto-activates on relevant prompts, a CLAUDE.md teaching Claude Code the project layout and conventions, and (in progress) a companion MCP server so Claude Code can call mcptest tools directly during an agent-development session.
```

**Recommendation Checklist**
- [x] I have checked that this resource hasn't already been submitted
- [x] It has been over one week since the first public commit to the repo I am recommending  *(only after 2026-04-22)*

## Pre-submission checklist

- [x] v0.1.0 published to PyPI
- [x] GitHub repo public with README
- [x] `.claude/skills/mcptest/SKILL.md` in place
- [x] CLAUDE.md at repo root
- [ ] Dev.to post published (do this first — gives social proof for the listing)
- [ ] ≥ 1 week since first commit (wait until 2026-04-22)
- [ ] Some GitHub stars accumulated (curated lists favor signals of traction)
- [ ] MCP server for mcptest itself merged (optional but strong — mention under Additional Comments)

## After submission

If accepted, the awesome-list listing drives GitHub → repo → PyPI traffic.
Track the star delta over the first 7 days to gauge impact. Approved
entries typically land in a batched Claude-authored PR within a few days.

If you don't hear back in a week, reply-bump on the issue. Don't open a
competing PR — the repo explicitly routes contributions through the issue
template.
