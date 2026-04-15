"""Schema-driven test suite generator.

Reads fixture ``input_schema`` JSON Schema declarations and produces complete
test suites covering happy paths, match cases, type errors, missing required
fields, edge/boundary values, and error injection — all without an LLM.

Usage::

    from mcptest.generate import TestGenerator, generate_suite
    from mcptest.fixtures.loader import load_fixture

    fixtures = [load_fixture("fixtures/github.yaml")]
    suite_dict = generate_suite(
        fixtures,
        name="my-suite",
        agent_cmd="python agent.py",
        fixture_paths=["fixtures/github.yaml"],
    )
"""

from __future__ import annotations

from mcptest.generate.engine import TestGenerator, generate_suite

__all__ = ["TestGenerator", "generate_suite"]
