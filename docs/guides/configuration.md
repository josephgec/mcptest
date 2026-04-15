# Configuration

mcptest discovers a `mcptest.yaml` (or `mcptest.yml`) file by walking up from
the current working directory — the same strategy git uses for `.gitignore`.
The first file found wins.

## Full reference

```yaml
# mcptest.yaml

# Paths -----------------------------------------------------------------------

# Test file directories (default: tests/).
# mcptest run will search here when no PATH argument is given.
test_paths:
  - tests/
  - integration/

# Fixture directories. Informational — passed to capture and generate commands.
fixture_paths:
  - fixtures/

# Where snapshot/diff baselines are stored (default: .mcptest/baselines).
baseline_dir: .mcptest/baselines


# Execution -------------------------------------------------------------------

# Default retry count for every case (default: per-case or 1).
retry: 3

# Default pass-rate tolerance for retried cases (0.0–1.0, default: per-case).
tolerance: 0.8

# Default parallel worker count (0 = auto-detect CPU count, default: 1).
parallel: 4

# Stop at the first failing case (default: false).
fail_fast: false

# CI coverage gate: exit non-zero if overall_score is below this value.
# Used by `mcptest coverage --threshold` when no --threshold flag is given.
fail_under: 0.0


# Metrics ---------------------------------------------------------------------

# Per-metric score thresholds for `mcptest scorecard`.
thresholds:
  tool_efficiency: 0.7
  redundancy: 0.3
  error_recovery_rate: 0.6


# Plugins ---------------------------------------------------------------------

# Modules to load at startup. Each entry is either a dotted Python module name
# or a relative/absolute file path (see the Plugins guide for details).
plugins:
  - my_company.mcptest_extensions
  - ./custom_assertions.py


# Cloud -----------------------------------------------------------------------

cloud:
  url: https://mcptest.example.com
  api_key_env: MCPTEST_API_KEY   # name of the env var that holds the key
```

## CLI flag precedence

CLI flags **always** win over config-file values.  Config-file values fill in
when the flag is omitted.

| Config field | CLI flag | Command |
|---|---|---|
| `test_paths[0]` | `PATH` argument | `mcptest run` |
| `retry` | `--retry` | `mcptest run` |
| `tolerance` | `--tolerance` | `mcptest run` |
| `parallel` | `-j` / `--parallel` | `mcptest run` |
| `fail_fast` | `--fail-fast` | `mcptest run` |
| `baseline_dir` | `--baseline-dir` | `mcptest snapshot` / `mcptest diff` |
| `fail_under` | `--threshold` | `mcptest coverage` |

## Inspecting the resolved config

```bash
mcptest config
```

Prints the active config file path, all resolved settings, and the list of
loaded plugins.  Useful when debugging unexpected behaviour.
