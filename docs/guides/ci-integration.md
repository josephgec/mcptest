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
