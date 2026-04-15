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

errors:
  - name: rate_limited
    tool: create_issue
    error_code: -32000
    message: GitHub API rate limit exceeded
```

## Assertions reference

See [Assertions Reference](../reference/assertions.md) for the full list.
