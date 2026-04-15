# Plugins

Plugins extend mcptest's built-in registries — assertions, metrics, and
exporters — without forking the project.  Any Python module that calls the
appropriate registration decorator at import time is a valid plugin.

## Three loading mechanisms

### 1. `confmcptest.py` (auto-discovered)

Drop a `confmcptest.py` file in your test directory (or any parent).
mcptest finds it automatically at startup — the same way pytest discovers
`conftest.py`.

```python
# tests/confmcptest.py
from mcptest.assertions.base import register_assertion, TraceAssertion
from mcptest.assertions.base import AssertionResult
from mcptest.runner.trace import Trace


@register_assertion
class response_is_json(TraceAssertion):
    yaml_key = "response_is_json"

    def check(self, trace: Trace) -> AssertionResult:
        ok = all(
            "json" in (call.result or "").lower()
            for call in trace.tool_calls
        )
        return AssertionResult(
            passed=ok,
            name=self.yaml_key,
            message="all tool responses are JSON" if ok else "non-JSON response found",
        )
```

### 2. `plugins:` list in `mcptest.yaml`

List dotted module names or relative/absolute file paths:

```yaml
# mcptest.yaml
plugins:
  - my_company.mcptest_extensions   # installed package
  - ./scripts/custom_assertions.py  # local file (relative to CWD)
```

### 3. Entry points (distributable packages)

For plugins you ship as a package, declare entry points in `pyproject.toml`:

```toml
[project.entry-points."mcptest.assertions"]
my_assertions = "my_package.assertions"

[project.entry-points."mcptest.metrics"]
my_metrics = "my_package.metrics"

[project.entry-points."mcptest.exporters"]
my_exporter = "my_package.exporters"
```

Once the package is installed (`pip install my-package`), mcptest loads it
automatically at startup — no config file changes needed.

## Writing custom assertions

```python
from mcptest.assertions.base import register_assertion, TraceAssertion
from mcptest.assertions.base import AssertionResult
from mcptest.runner.trace import Trace


@register_assertion
class latency_under_2s(TraceAssertion):
    yaml_key = "latency_under_2s"

    def check(self, trace: Trace) -> AssertionResult:
        slow = [c for c in trace.tool_calls if (c.duration_ms or 0) > 2000]
        return AssertionResult(
            passed=not slow,
            name=self.yaml_key,
            message="all calls < 2 s" if not slow else f"{len(slow)} slow call(s)",
        )
```

Then use it in any test YAML:

```yaml
assertions:
  - latency_under_2s: true
```

## Writing custom metrics

```python
from mcptest.metrics.base import register_metric, Metric, MetricResult
from mcptest.runner.trace import Trace


@register_metric
class avg_call_count(Metric):
    name = "avg_call_count"
    label = "Avg tool calls"

    def compute(self, trace: Trace, **kwargs) -> MetricResult:
        n = len(trace.tool_calls)
        score = max(0.0, 1.0 - n / 20)  # penalise > 20 calls
        return MetricResult(
            name=self.name,
            score=score,
            label=self.label,
            details={"call_count": n},
        )
```

## Verifying loaded plugins

```bash
mcptest config
```

The output lists every plugin that was loaded and its source (file path,
dotted name, or entry-point group).
