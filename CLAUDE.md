# mcptest — project guide for Claude Code

**One-liner:** pytest for MCP agents. YAML mock servers, trajectory assertions,
regression diffing, 6 pre-built fixture packs, MIT licensed.

**PyPI:** `mcp-agent-test` (import name + CLI are both `mcptest`)

---

## What this project does

mcptest is a testing framework for Model Context Protocol (MCP) agents.
It lets users:

1. **Mock MCP servers from YAML** — `fixtures/*.yaml` files declaring tools,
   responses, and error scenarios that speak real MCP over stdio/SSE/HTTP.
2. **Write trajectory assertions** in YAML (`tests/test_*.yaml`) — assert
   which tools got called, in what order, with what arguments, how often,
   how fast.
3. **Diff against a baseline** — snapshot known-good trajectories, change
   the agent prompt/model, catch silent regressions before merge.

The core value is that all of this is **hermetic** (no real API calls, no
tokens, deterministic under retry) and **MCP-native** (the primitives match
how MCP agents actually work, not a ported generic LLM-eval DSL).

## Repo layout

```
src/mcptest/              The package. Everything the user imports/runs.
  agents/                 Bundled scripted agents (used by fixture packs)
  assertions/             17 trajectory assertions — impls.py, combinators.py
  bench/                  mcptest bench (multi-agent leaderboard)
  capture/                mcptest capture (live MCP server → fixture YAML)
  cli/                    Click-based CLI entry points
  cloud/                  FastAPI backend + dashboard + webhooks
  compare/                mcptest compare (two-trace diff)
  conformance/            19 MCP protocol conformance checks
  coverage/               Fixture surface-area coverage analysis
  diff/                   Trace diffing engine (mcptest diff --ci)
  docs/                   Auto-generated reference docs (mcptest docs build)
  eval/                   Semantic evaluation (keyword/regex/similarity)
  exporters/              JUnit/TAP/HTML output formats
  fixtures/               Fixture YAML parser (models.py, loader.py)
  generate/               Fixture-schema → test-YAML generator
  metrics/                7 quality metrics — impls.py
  mock_server/            MCP server that reads a fixture YAML (stdio + SSE)
  registry/               Built-in test packs (packs.py has all 6)
  runner/                 Test runner, agent adapters, Trace dataclass
  testspec/               Test YAML parser (models.py, loader.py)
  watch/                  mcptest watch — file-watching re-runner

tests/                    ~1,800 tests. Mirrors src layout.
examples/                 User-facing examples, including quickstart/
docs/                     MkDocs source + blog posts under docs/blog/
```

## Running tests

```bash
# Fast path (assumes venv active)
.venv/bin/python -m pytest tests/ -x -q

# Or via uv
uv run pytest tests/ -x -q

# Single file
.venv/bin/python -m pytest tests/test_registry.py -xvs
```

Expected state: **1,801 tests passing, 93% coverage**. If a PR drops below
either of those, something broke.

## Running mcptest itself

```bash
.venv/bin/mcptest run                # run tests under ./tests/
.venv/bin/mcptest init ./demo        # scaffold a new project
.venv/bin/mcptest install-pack github ./demo
.venv/bin/mcptest conformance "python server.py"
.venv/bin/mcptest dashboard          # needs [cloud] extras
```

## Adding a new assertion

1. Add the class to `src/mcptest/assertions/impls.py` (or `combinators.py` for
   boolean combinators). Decorate with `@register_assertion` and set
   `yaml_key: ClassVar[str] = "your_name"`.
2. Add tests to `tests/test_assertions.py`.
3. Docs are auto-generated — no manual doc update needed.

## Adding a new fixture pack

1. Edit `src/mcptest/registry/packs.py`:
   - Write `_FOO_FIXTURE` (fixture YAML as a string literal)
   - Write `_FOO_TESTS` (test YAML with real assertions, not `max_tool_calls: 5` placeholders)
   - Add to the `PACKS` dict at the bottom
2. Test YAML fixture refs must be `../fixtures/foo.yaml` (not `fixtures/foo.yaml`) because packs install with `fixtures/` and `tests/` as siblings.
3. Agent command should be `python -m mcptest.agents.scripted` — it works out of the box and accepts `tool_name key=value` stdin input.
4. Update `EXPECTED_PACKS` in `tests/test_registry.py`.

Verify the pack works end-to-end:
```bash
rm -rf /tmp/pack_check && .venv/bin/mcptest install-pack foo /tmp/pack_check \
  && cd /tmp/pack_check && /path/to/.venv/bin/mcptest run
```

All assertions should pass green.

## Conventions

- **Never mock the database in tests.** Integration tests use real SQLite
  files under `tmp_path` so prod-parity is preserved.
- **No placeholder assertions** (`max_tool_calls: 5` on empty input, etc.).
  Every test case must exercise a real tool call with a real assertion.
- **Assertions use substring matching against error *messages*, not error *names*.**
  For example, `error_handled: "Path traversal"` matches the message
  "Path traversal attempt blocked" in the filesystem pack.
- **Filterwarnings is `error` in `pyproject.toml`.** A new DeprecationWarning
  will break CI unless explicitly ignored.
- **Coverage omits `pytest_plugin.py` and `__init__.py`** — they load before
  pytest-cov starts instrumenting. Their behavior is covered via nested
  pytester sessions in `tests/test_pytest_plugin.py`.

## Commit style

Session-numbered when part of the claude-loop ("Session 33: ..."), otherwise
imperative one-liner + body explaining *why*. Co-Authored-By trailer is fine.

## Contact

Questions and bug reports: https://github.com/josephgec/mcptest/issues
