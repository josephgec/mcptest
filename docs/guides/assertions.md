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
