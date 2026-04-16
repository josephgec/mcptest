# The missing CI step: blocking PRs when agent behavior regresses

*A real-world example of how `mcptest diff --ci` catches silent agent
regressions that unit tests miss — with a full GitHub Actions workflow
and an annotated "this PR is blocked" walkthrough.*

---

## The shape of the bug

Someone opens a PR with a one-line change:

```diff
--- a/agent/prompts.py
+++ b/agent/prompts.py
@@ -12,7 +12,7 @@ SYSTEM_PROMPT = """
 You are an issue triage assistant for the acme/api repository.
-Before filing a new issue, ALWAYS check list_issues for duplicates.
+Be helpful and respond to the user's request.
 """
```

All their tests pass. Lint passes. Type-check passes. CI is green.

The PR ships. Three weeks later someone notices the duplicate-issue count on
the repo has quintupled. The agent stopped calling `list_issues` before
`create_issue`. No test detected it because no test checked the *trajectory*.

## The fix: one CI job

```yaml
# .github/workflows/agent-ci.yml
name: Agent CI
on:
  pull_request:
    paths:
      - "agent/**"
      - "fixtures/**"
      - "tests/**"
      - "pyproject.toml"

jobs:
  mcptest:
    name: Agent behavior tests
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write     # for the PR comment

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0       # needed so mcptest can resolve baselines from main

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install
        run: pip install mcp-agent-test

      - name: Run agent behavior tests
        id: run
        run: mcptest run --json > results.json
        continue-on-error: true

      - name: Diff against main-branch baselines
        id: diff
        run: |
          git fetch origin main
          git checkout origin/main -- .mcptest/baselines
          mcptest diff --ci --baseline-dir .mcptest/baselines
        continue-on-error: true

      - name: Post PR comment
        if: always()
        run: mcptest github-comment --results results.json
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Fail job on regression
        if: steps.run.outcome != 'success' || steps.diff.outcome != 'success'
        run: exit 1
```

That's it. One job. Installed via PyPI. No custom Docker images, no secrets
other than the default `GITHUB_TOKEN`.

## What a blocked PR looks like

When someone opens the PR above, the Agent CI job fails within 90 seconds
and this comment lands on the pull request:

---

> ### 🤖 Agent behavior regression detected
>
> `mcptest diff` found **2 regressions** across 5 test cases.
> Merging this PR would change how the agent behaves in production.
>
> | Test case | Regression |
> |-----------|-----------|
> | `triage agent::checks duplicates before creating` | `tool_order`: expected `list_issues → create_issue`, got `create_issue` |
> | `triage agent::comments on existing duplicate` | `tool_called`: expected `add_comment` to be called, it wasn't |
>
> <details><summary>Trajectory diff for <code>checks duplicates before creating</code></summary>
>
> ```diff
>   input: "login page returns 500 on Safari"
> - [1] list_issues(repo="acme/api", query="login 500") → { "issues": [] }
>   [2] create_issue(repo="acme/api", title="login 500 on Safari") → { "number": 42 }
> ```
>
> The agent dropped the `list_issues` call. This means it will file duplicate
> issues in production.
>
> </details>
>
> | Metric | Baseline | Current | Δ |
> |--------|----------|---------|---|
> | tool_efficiency | 1.00 | 1.00 | — |
> | tool_coverage | 1.00 | 0.60 | **-0.40** 🔻 |
> | error_recovery_rate | 1.00 | 0.75 | **-0.25** 🔻 |
>
> If this behavior change is intentional, run locally:
>
> ```bash
> mcptest snapshot --update
> git add .mcptest/baselines && git commit -m "update baseline: ..."
> ```
>
> and push the updated baselines. Otherwise, restore the behavior before merging.

---

The "Required" status check next to the PR goes red. The merge button is
greyed out. A reviewer can see, in 20 seconds of glance, exactly what changed
and why the change is dangerous.

## Why this is different

Every other testing tool in this stack answers "did the code still run?"
`mcptest diff --ci` answers "did the *agent behavior* still match what it
used to match?" Those are different questions. A prompt change never fails
a unit test. A model swap never fails a lint check. A subtly broken
system-prompt edit passes every generic eval tool.

The missing CI step is trajectory-level regression gating, and it's the one
that matches the actual shape of agent bugs.

## Adopting this in your repo

```bash
# 1. Install
pip install mcp-agent-test

# 2. Scaffold
mcptest init

# 3. Write one fixture and one test case (or install-pack a pre-built one)
mcptest install-pack github .

# 4. Run, confirm green
mcptest run

# 5. Snapshot baselines and commit them
mcptest snapshot
git add .mcptest/baselines
git commit -m "add mcptest baselines"

# 6. Copy the workflow above into .github/workflows/agent-ci.yml
```

That's the whole onboarding. Ten minutes. You now have trajectory-level CI
for every future PR that touches your agent.

Source: <https://github.com/josephgec/mcptest>
PyPI: <https://pypi.org/project/mcp-agent-test/>
