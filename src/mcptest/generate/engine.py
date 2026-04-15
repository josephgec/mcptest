"""Test suite generator engine.

Reads Fixture objects and emits a complete TestSuite-compatible dict that can
be YAML-serialised and run with ``mcptest run``.

Six test-case categories are produced:

* ``happy``      — one valid call per tool; asserts tool_called + no_errors
* ``match``      — one case per non-default response with match conditions
* ``type_error`` — one case per required field with the wrong type; asserts error_handled
* ``missing``    — one case per required field with that field omitted; asserts error_handled
* ``edge``       — boundary values (empty string, 0, -1, very-long, empty array)
* ``error``      — one case per fixture error scenario; asserts error_handled

Usage::

    from mcptest.generate import generate_suite
    from mcptest.fixtures.loader import load_fixture

    fixtures = [load_fixture("fixtures/github.yaml")]
    suite = generate_suite(
        fixtures,
        name="github-generated",
        agent_cmd="python agent.py",
        fixture_paths=["fixtures/github.yaml"],
    )
"""

from __future__ import annotations

import json
import re
from typing import Any

from mcptest.fixtures.models import Fixture, ToolSpec
from mcptest.generate.values import (
    generate_edge_cases,
    generate_from_match,
    generate_missing_required,
    generate_type_error,
    generate_valid,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CATEGORIES: frozenset[str] = frozenset(
    ["happy", "match", "type_error", "missing", "edge", "error"]
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class TestGenerator:
    """Generate a complete test suite from a list of :class:`Fixture` objects."""

    def __init__(self, fixtures: list[Fixture]) -> None:
        self._fixtures = fixtures

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def generate_suite(
        self,
        name: str,
        agent_cmd: str,
        *,
        categories: list[str] | None = None,
        timeout_s: float = 60.0,
        fixture_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a TestSuite-compatible dict for *name*.

        Parameters
        ----------
        name:
            Suite name embedded in the output YAML.
        agent_cmd:
            Shell command used to invoke the agent (e.g. ``"python agent.py"``).
        categories:
            Subset of ``{"happy","match","type_error","missing","edge","error"}``.
            Defaults to all six.
        timeout_s:
            Per-case agent timeout written into ``agent.timeout_s``.
        fixture_paths:
            Relative paths to embed in the ``fixtures:`` list.  Defaults to
            empty (caller can fill in after the fact).
        """
        if categories is None:
            categories = list(VALID_CATEGORIES)

        unknown = set(categories) - VALID_CATEGORIES
        if unknown:
            raise ValueError(
                f"unknown categories: {sorted(unknown)!r}; "
                f"valid choices are: {sorted(VALID_CATEGORIES)!r}"
            )

        cases: list[dict[str, Any]] = []
        for fixture in self._fixtures:
            for tool in fixture.tools:
                if "happy" in categories:
                    cases.extend(self._happy_path_cases(tool))
                if "match" in categories:
                    cases.extend(self._match_cases(tool))
                if "type_error" in categories:
                    cases.extend(self._type_error_cases(tool))
                if "missing" in categories:
                    cases.extend(self._missing_required_cases(tool))
                if "edge" in categories:
                    cases.extend(self._edge_case_cases(tool))
                if "error" in categories:
                    cases.extend(self._error_injection_cases(fixture, tool))

        return {
            "name": name,
            "fixtures": list(fixture_paths or []),
            "agent": {
                "command": agent_cmd,
                "timeout_s": timeout_s,
            },
            "cases": cases,
        }

    # ------------------------------------------------------------------
    # Private per-category generators
    # ------------------------------------------------------------------

    def _make_input(self, tool_name: str, args: dict) -> str:
        """Serialise a tool call to the JSON string format expected by agents."""
        return json.dumps({"tool": tool_name, "args": args})

    def _happy_path_cases(self, tool: ToolSpec) -> list[dict[str, Any]]:
        args = generate_valid(tool.input_schema)
        return [
            {
                "name": f"{tool.name}-happy-path",
                "input": self._make_input(tool.name, args),
                "assertions": [
                    {"tool_called": tool.name},
                    {"no_errors": True},
                ],
            }
        ]

    def _match_cases(self, tool: ToolSpec) -> list[dict[str, Any]]:
        """One case per non-default response that declares a ``match`` dict."""
        cases: list[dict[str, Any]] = []
        base_args = generate_valid(tool.input_schema)

        for response in tool.responses:
            if response.default or not response.match:
                continue
            match_args = generate_from_match(response.match)
            args = {**base_args, **match_args}
            # Build a short stable label from the first two match values.
            label = "-".join(
                str(v) for v in list(response.match.values())[:2]
            )
            label = _sanitize(label) or "match"
            cases.append(
                {
                    "name": f"{tool.name}-match-{label}",
                    "input": self._make_input(tool.name, args),
                    "assertions": [
                        {"tool_called": tool.name},
                    ],
                }
            )
        return cases

    def _type_error_cases(self, tool: ToolSpec) -> list[dict[str, Any]]:
        """One case per required field, substituting the wrong type."""
        required: list[str] = tool.input_schema.get("required", [])
        cases: list[dict[str, Any]] = []
        for field in required:
            args = generate_type_error(tool.input_schema, field)
            cases.append(
                {
                    "name": f"{tool.name}-type-error-{field}",
                    "input": self._make_input(tool.name, args),
                    "assertions": [
                        {"error_handled": True},
                    ],
                }
            )
        return cases

    def _missing_required_cases(self, tool: ToolSpec) -> list[dict[str, Any]]:
        """One case per required field, omitting that field."""
        required: list[str] = tool.input_schema.get("required", [])
        cases: list[dict[str, Any]] = []
        for field in required:
            args = generate_missing_required(tool.input_schema, field)
            cases.append(
                {
                    "name": f"{tool.name}-missing-{field}",
                    "input": self._make_input(tool.name, args),
                    "assertions": [
                        {"error_handled": True},
                    ],
                }
            )
        return cases

    def _edge_case_cases(self, tool: ToolSpec) -> list[dict[str, Any]]:
        """Boundary-value cases from :func:`generate_edge_cases`."""
        edge_cases = generate_edge_cases(tool.input_schema)
        cases: list[dict[str, Any]] = []
        for ec in edge_cases:
            cases.append(
                {
                    "name": f"{tool.name}-edge-{ec.label}",
                    "input": self._make_input(tool.name, ec.args),
                    "assertions": [
                        {"tool_called": tool.name},
                    ],
                }
            )
        return cases

    def _error_injection_cases(
        self, fixture: Fixture, tool: ToolSpec
    ) -> list[dict[str, Any]]:
        """One case per fixture error that targets this tool or is global."""
        base_args = generate_valid(tool.input_schema)
        cases: list[dict[str, Any]] = []
        for error in fixture.errors:
            # Include global errors (no tool scope) and tool-specific ones.
            if error.tool is not None and error.tool != tool.name:
                continue
            cases.append(
                {
                    "name": f"{tool.name}-error-{error.name}",
                    "input": self._make_input(tool.name, base_args),
                    "inject_error": error.name,
                    "assertions": [
                        {"error_handled": True},
                    ],
                }
            )
        return cases


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


def generate_suite(
    fixtures: list[Fixture],
    name: str,
    agent_cmd: str,
    *,
    categories: list[str] | None = None,
    timeout_s: float = 60.0,
    fixture_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a TestSuite dict from *fixtures*.

    Equivalent to ``TestGenerator(fixtures).generate_suite(...)``.
    """
    return TestGenerator(fixtures).generate_suite(
        name,
        agent_cmd,
        categories=categories,
        timeout_s=timeout_s,
        fixture_paths=fixture_paths,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize(s: str) -> str:
    """Replace non-alphanumeric runs with hyphens and trim to 40 chars."""
    sanitized = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-")
    return sanitized[:40]
