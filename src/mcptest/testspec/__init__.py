"""TestSuite / TestCase models and YAML loader.

A *test file* is a YAML document describing a single agent-under-test, the
mock fixtures it should run against, and one or more input/assertion pairs.

```yaml
name: Issue triage
fixtures:
  - fixtures/github.yaml
agent:
  command: python examples/issue_agent.py
cases:
  - name: Creates issue for bug report
    input: "File a bug: login 500 on Safari"
    assertions:
      - tool_called: create_issue
      - param_matches: { tool: create_issue, param: title, contains: "500" }
```
"""

from __future__ import annotations

from mcptest.testspec.loader import TestSuiteLoadError, load_test_suite, load_test_suites
from mcptest.testspec.models import AgentSpec, TestCase, TestSuite

__all__ = [
    "AgentSpec",
    "TestCase",
    "TestSuite",
    "TestSuiteLoadError",
    "load_test_suite",
    "load_test_suites",
]
