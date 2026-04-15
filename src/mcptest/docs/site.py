"""MkDocs site builder for the documentation engine.

Generates a full MkDocs site into an output directory:
- ``mkdocs.yml``           — site config with Material theme
- ``docs/index.md``        — landing page
- ``docs/getting-started.md``
- ``docs/guides/*.md``     — narrative guides
- ``docs/reference/*.md``  — auto-generated from live registries

Usage::

    from mcptest.docs.site import build_site
    paths = build_site()             # default: ./site-output/
    paths = build_site("my-docs/")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# MkDocs configuration template
# ---------------------------------------------------------------------------

MKDOCS_CONFIG: dict[str, Any] = {
    "site_name": "mcptest",
    "site_description": "pytest for MCP agents — testing framework for Model Context Protocol agents",
    "repo_url": "https://github.com/josephgec/mcptest",
    "theme": {
        "name": "material",
        "palette": [
            {"scheme": "default", "primary": "indigo", "accent": "indigo"},
            {"scheme": "slate", "primary": "indigo", "accent": "indigo"},
        ],
        "features": [
            "navigation.tabs",
            "navigation.sections",
            "navigation.top",
            "search.highlight",
            "content.code.copy",
        ],
    },
    "nav": [
        {"Home": "index.md"},
        {"Getting Started": "getting-started.md"},
        {
            "Guides": [
                {"Writing Tests": "guides/writing-tests.md"},
                {"Assertions": "guides/assertions.md"},
                {"Conformance Checks": "guides/conformance.md"},
                {"CI Integration": "guides/ci-integration.md"},
            ]
        },
        {
            "Reference": [
                {"Assertions": "reference/assertions.md"},
                {"Metrics": "reference/metrics.md"},
                {"Conformance Checks": "reference/checks.md"},
                {"CLI": "reference/cli.md"},
            ]
        },
    ],
    "markdown_extensions": [
        "admonition",
        "pymdownx.highlight",
        "pymdownx.superfences",
        "pymdownx.tabbed",
        "pymdownx.details",
        "tables",
        "toc",
    ],
}

# ---------------------------------------------------------------------------
# Static guide page content
# ---------------------------------------------------------------------------

_INDEX_MD = """\
# mcptest

**pytest for MCP agents.**  A testing framework for Model Context Protocol (MCP) agents
that lets you mock MCP servers with YAML fixtures, run agents against them in isolation,
and assert against the resulting tool-call trajectories.

```bash
pip install mcptest
mcptest capture "python my_server.py" --output fixtures/ --generate-tests
mcptest run
```

## Features

| Feature | Description |
|---------|-------------|
| **Capture** | Auto-generate fixtures and tests from a live MCP server |
| **Assertions** | 15+ trajectory assertions (tool calls, ordering, parameters, output) |
| **Metrics** | 7 quality metrics (efficiency, redundancy, stability, …) |
| **Conformance** | 19 protocol checks across 5 sections (MUST / SHOULD / MAY) |
| **Watch mode** | Smart file watching — re-run only affected tests on save |
| **CI/CD** | GitHub Action, PR comment bot, badge generation |
| **Cloud backend** | Store traces and metrics history |
| **Parallel runs** | `-j N` for concurrent test execution |

## Quick navigation

- [Getting Started](getting-started.md) — 5-minute quickstart using `mcptest capture`
- [Assertions Reference](reference/assertions.md) — every assertion with examples
- [Metrics Reference](reference/metrics.md) — quality metrics guide
- [CLI Reference](reference/cli.md) — all 23 commands documented
"""

_GETTING_STARTED_MD = """\
# Getting Started

The fastest path from zero to running tests is `mcptest capture`.  Point it at
any MCP server and it auto-discovers tools, samples responses, and writes both
fixture YAML and test-spec YAML — no hand-writing required.

## 1. Install

```bash
pip install mcptest
```

## 2. Capture a live server

```bash
mcptest capture "python my_server.py" --output fixtures/ --generate-tests
```

This will:

1. Start your server subprocess and connect over stdio
2. Call `list_tools()` to discover all available tools
3. Sample each tool with varied arguments (`--samples-per-tool 3` by default)
4. Write `fixtures/my-server.yaml` with real server responses
5. Write `fixtures/my-server-tests.yaml` with a ready-to-run test suite

## 3. Run the generated tests

```bash
mcptest run fixtures/my-server-tests.yaml
```

## 4. Iterate with watch mode

```bash
mcptest watch --watch-extra src/
```

`mcptest watch` monitors your test files, fixtures, and source directories.
It re-runs only the tests affected by each change — perfect for tight
feedback loops during development.

## 5. Inline help

```bash
# Look up any assertion, metric, or check by name
mcptest explain tool_called
mcptest explain tool_efficiency
mcptest explain INIT-001

# List all available names
mcptest docs list
```

## Next steps

- [Writing Tests](guides/writing-tests.md) — manual YAML test authoring
- [Assertions Reference](reference/assertions.md) — all assertions with examples
- [CI Integration](guides/ci-integration.md) — GitHub Action setup
"""

_WRITING_TESTS_MD = """\
# Writing Tests

Tests are written in YAML files.  Each file defines one **test suite** with a
list of **test cases**.

## Minimal example

```yaml
# tests/issue_triage.yaml
name: Issue triage agent
fixtures:
  - fixtures/github.yaml
agent:
  command: python my_agent.py
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

## Test suite fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Suite name shown in output |
| `fixtures` | Yes | List of fixture YAML paths |
| `agent.command` | Yes | Agent subprocess command |
| `cases` | Yes | List of test case objects |

## Test case fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | Yes | — | Case name shown in output |
| `input` | Yes | — | Prompt sent to the agent |
| `assertions` | No | `[]` | Assertion list |
| `retry` | No | 1 | Number of retry attempts |
| `tolerance` | No | 0.0 | Fraction of retries allowed to fail |

## Fixture format

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
      - default: true
        return:
          issue_number: 1
```

## Assertions reference

See [Assertions Reference](../reference/assertions.md) for the full list.
"""

_ASSERTIONS_GUIDE_MD = """\
# Using Assertions

Assertions are the heart of mcptest.  They verify specific behaviors of your
agent's tool-call trajectory after a trace is recorded.

## Basic usage

Each assertion is a single-key YAML entry under `assertions:`:

```yaml
assertions:
  - tool_called: create_issue       # agent invoked this tool
  - max_tool_calls: 5               # no more than 5 total calls
  - output_contains: "created #42"  # output contains this string
```

## Metric-gated assertions

Use quality metrics as assertion gates:

```yaml
assertions:
  - metric_above: {metric: tool_efficiency, threshold: 0.8}
  - metric_below: {metric: redundancy, threshold: 0.2}
```

## Boolean combinators

Combine assertions with boolean logic:

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
```

## Weighted score gate

Gate on a composite quality score:

```yaml
assertions:
  - weighted_score:
      threshold: 0.75
      weights:
        tool_efficiency: 0.3
        redundancy: 0.2
        error_recovery_rate: 0.5
```

## Terminal help

Look up any assertion instantly:

```bash
mcptest explain tool_called
mcptest explain param_matches
```

## Full reference

See the [Assertions Reference](../reference/assertions.md) for complete
parameter documentation and examples for all 19 assertions.
"""

_CONFORMANCE_GUIDE_MD = """\
# Conformance Testing

Conformance checks verify that any MCP server correctly implements the
protocol.  19 checks across 5 sections, each tagged with an RFC 2119
severity level (MUST / SHOULD / MAY).

## Quick start

```bash
# Test a server subprocess over stdio
mcptest conformance "python my_server.py"

# Test in-process using a fixture YAML (fast, no subprocess)
mcptest conformance --fixture fixtures/my_server.yaml

# Filter by section or severity
mcptest conformance --fixture fixtures/my_server.yaml --section initialization
mcptest conformance --fixture fixtures/my_server.yaml --severity must
```

## Severity levels

| Level | Meaning |
|-------|---------|
| **MUST** | Mandatory — failing this violates the protocol |
| *SHOULD* | Strongly recommended — failing is a significant issue |
| MAY | Optional — failing is a minor concern |

Exit code is 1 when any MUST check fails (or when SHOULD checks fail with
`--fail-on-should`).

## Check sections

| Section | ID Range | Checks |
|---------|----------|--------|
| Initialization | INIT-001–004 | Server name, version, capabilities |
| Tool listing | TOOL-001–004 | Schema validity, uniqueness |
| Tool calling | CALL-001–005 | Invocation, results, error responses |
| Error handling | ERR-001–003 | Bad inputs, null arguments |
| Resources | RES-001–003 | Resource listing (auto-skipped if no resources) |

## Full reference

See the [Conformance Checks Reference](../reference/checks.md) for the full
list with per-check documentation.
"""

_CI_INTEGRATION_MD = """\
# CI Integration

mcptest is designed to run in CI pipelines with zero extra configuration.

## GitHub Action

```yaml
# .github/workflows/mcptest.yml
name: mcptest
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install mcptest
      - run: mcptest run --ci
```

Or use the bundled action:

```yaml
- uses: josephgec/mcptest@v1
  with:
    test-path: tests/
    fail-under: 0.8
```

## PR comment bot

Post test results as a GitHub PR comment:

```bash
mcptest run --output results.json
mcptest github-comment --results results.json
```

Set `GITHUB_TOKEN` in your environment or pass `--token`.

## Badge generation

Generate a coverage badge for your README:

```bash
mcptest badge --results results.json --output badge.svg
```

## Conformance in CI

```yaml
- name: MCP protocol conformance
  run: mcptest conformance --fixture fixtures/server.yaml --severity must --json > conformance.json
```

## Coverage gating

```bash
# Fail if tool coverage < 80%
mcptest coverage tests/ --fail-under 0.8
```

## Parallel execution

Speed up large test suites with parallel workers:

```bash
mcptest run tests/ -j 4
```
"""


# ---------------------------------------------------------------------------
# Site builder
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _mkdocs_yml_content(config: dict[str, Any]) -> str:
    """Render the config dict as YAML text without a heavy dependency."""
    import yaml  # PyYAML is always available (core dep)

    return yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True)


def build_site(output_dir: str | Path | None = None) -> list[Path]:
    """Generate a full MkDocs documentation site into *output_dir*.

    Args:
        output_dir: Target directory (default: ``site-output/`` in the current
                    working directory).

    Returns:
        List of ``Path`` objects for every file written.
    """
    from mcptest.docs.generators import (
        generate_assertion_reference,
        generate_check_reference,
        generate_cli_reference,
        generate_metric_reference,
    )
    from mcptest.docs.extractors import (
        extract_assertions,
        extract_checks,
        extract_cli_commands,
        extract_metrics,
    )
    from mcptest.cli.main import main as cli_main

    root = Path(output_dir) if output_dir is not None else Path("site-output")

    written: list[Path] = []

    def write(rel: str, content: str) -> None:
        p = root / rel
        _write(p, content)
        written.append(p)

    # mkdocs.yml
    write("mkdocs.yml", _mkdocs_yml_content(MKDOCS_CONFIG))

    # Static guide pages
    write("docs/index.md", _INDEX_MD)
    write("docs/getting-started.md", _GETTING_STARTED_MD)
    write("docs/guides/writing-tests.md", _WRITING_TESTS_MD)
    write("docs/guides/assertions.md", _ASSERTIONS_GUIDE_MD)
    write("docs/guides/conformance.md", _CONFORMANCE_GUIDE_MD)
    write("docs/guides/ci-integration.md", _CI_INTEGRATION_MD)

    # Auto-generated reference pages
    write(
        "docs/reference/assertions.md",
        generate_assertion_reference(extract_assertions()),
    )
    write(
        "docs/reference/metrics.md",
        generate_metric_reference(extract_metrics()),
    )
    write(
        "docs/reference/checks.md",
        generate_check_reference(extract_checks()),
    )
    write(
        "docs/reference/cli.md",
        generate_cli_reference(extract_cli_commands(cli_main)),
    )

    return written
