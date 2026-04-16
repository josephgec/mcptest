# mcptest demo script (for asciinema / video capture)

Target runtime: **~95 seconds**. Tells one clear story: install →
green → break → regression caught. Designed so the gif fits in a
README (small file size, readable text).

## Before recording

```bash
# 1. Start in a clean temp dir
rm -rf /tmp/mcptest-demo && mkdir /tmp/mcptest-demo && cd /tmp/mcptest-demo

# 2. Pre-install so the demo doesn't show pip download noise
python -m venv .venv && source .venv/bin/activate
pip install --quiet mcp-agent-test

# 3. Set a big, readable font in your terminal (16pt+)
# 4. Resize terminal to ~100 cols × 30 rows (fits standard gif embeds)

# 5. Start recording
asciinema rec demo.cast
```

## The script (type these in order, don't paste)

Each block is roughly one "beat". Pause 1–2s between beats so viewers
can read. Total typed commands: 8.

---

### Beat 1 — Install & scaffold (10s)

```
# mcptest — pytest for MCP agents.
```

```
mcptest install-pack github ./my-agent
cd my-agent
```

**What appears:**
```
✓ installed pack github into ./my-agent
  created fixtures/github.yaml
  created tests/test_github.yaml
```

---

### Beat 2 — Show what we got (8s)

```
ls fixtures tests
```

**What appears:**
```
fixtures/:
github.yaml

tests/:
test_github.yaml
```

---

### Beat 3 — Run the tests, see green (10s)

```
mcptest run
```

**What appears:** Rich table with 5 passing rows in green:

```
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Suite       ┃ Case                        ┃ Status ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ github pack │ lists open issues           │  PASS  │
│ github pack │ opens a bug report          │  PASS  │
│ github pack │ refuses to file on missing  │  PASS  │
│ github pack │ merges a clean PR           │  PASS  │
│ github pack │ refuses to merge a draft    │  PASS  │
└─────────────┴─────────────────────────────┴────────┘
5 passed, 0 failed (5 total)  ⏱ 3.5s
```

---

### Beat 4 — Snapshot as baseline (6s)

```
mcptest snapshot
```

**What appears:**
```
✓ saved baseline for github pack::lists open issues
✓ saved baseline for github pack::opens a bug report
✓ saved baseline for github pack::refuses to file on missing repo
✓ saved baseline for github pack::merges a clean PR
✓ saved baseline for github pack::refuses to merge a draft
5 saved, 0 skipped
```

---

### Beat 5 — Break something (12s)

```
# Pretend we just shipped a "fix" that removes the duplicate check.
```

Use `sed` to make a visible change — something the viewer can read.
The exact command depends on the pack; here we neuter the duplicate-check
response path to simulate a prompt regression:

```
sed -i.bak 's/gh_list_issues/gh_list_issues_disabled/g' fixtures/github.yaml
```

Or — more dramatic — swap out the agent command to one that *doesn't*
call `list_issues`:

```
cat > broken-agent.py <<'PY'
# Intentionally broken: skips the duplicate-check step.
import asyncio, json, os, sys
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

async def main():
    fx = json.loads(os.environ["MCPTEST_FIXTURES"])[0]
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "mcptest.mock_server", fx], env=os.environ.copy())
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await s.call_tool("gh_create_issue",
                              arguments={"repo": "acme/api", "title": "bug"})
asyncio.run(main())
PY

sed -i.bak 's|python -m mcptest.agents.scripted|python broken-agent.py|' tests/test_github.yaml
```

(For the actual gif you only need ONE of these shown on screen — pick
the shorter one.)

---

### Beat 6 — Diff catches it (15s)

```
mcptest diff --ci
```

**What appears:** Red regression output:

```
✗ github pack::lists open issues REGRESSION
  tool_called: expected gh_list_issues to be called, it wasn't

✗ github pack::opens a bug report REGRESSION
  tool_order: expected list → create, got create only

... (3 more)

5 regression(s) across 5 case(s)
Exit: 1
```

Then show the exit code explicitly:

```
echo "exit: $?"
```

**What appears:**
```
exit: 1
```

---

### Beat 7 — The punchline (10s)

Type this last line slowly for emphasis:

```
# CI just blocked the PR. You've got a safety net for agent behavior.
```

Hit `Ctrl+D` to stop recording.

---

## Post-processing

```bash
# Convert to gif (via agg — https://github.com/asciinema/agg)
agg --theme monokai --font-size 16 demo.cast demo.gif

# Or upload the .cast directly
asciinema upload demo.cast
```

## Embedding

In your README, right under the title:

```markdown
![mcptest demo](docs/blog/demo.gif)
```

Or embed the asciinema link:

```markdown
[![asciicast](https://asciinema.org/a/XXXXX.svg)](https://asciinema.org/a/XXXXX)
```

## Beats recap

| Beat | Content | Seconds |
|------|---------|---------|
| 1 | Install a pack, cd in | 10 |
| 2 | `ls` to show files | 8 |
| 3 | `mcptest run` → all green | 10 |
| 4 | `mcptest snapshot` | 6 |
| 5 | Break something (visible edit) | 12 |
| 6 | `mcptest diff --ci` → red regressions + exit 1 | 15 |
| 7 | Punchline comment | 10 |
| **Total** | | **~70s** (plus reading pauses → ~95s) |

## What NOT to include

- Don't show the `pip install` — it's 10 seconds of nothing interesting.
- Don't show the fixture file contents — it's too much text for a gif.
- Don't show configuration flags or alternative commands — one happy path.
- Don't show stderr noise — redirect if the MCP SDK is chatty.
