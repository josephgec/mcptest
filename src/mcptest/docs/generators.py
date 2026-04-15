"""Markdown generators for the documentation engine.

Each generator accepts pre-extracted metadata dicts (from extractors.py)
and produces a Markdown string.  Outputs are deterministic and testable.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# YAML example snippets — one per assertion yaml_key
# ---------------------------------------------------------------------------

_ASSERTION_EXAMPLES: dict[str, str] = {
    "tool_called": "assertions:\n  - tool_called: create_issue",
    "tool_not_called": "assertions:\n  - tool_not_called: delete_all",
    "tool_call_count": "assertions:\n  - tool_call_count:\n      tool: search\n      count: 3",
    "max_tool_calls": "assertions:\n  - max_tool_calls: 5",
    "param_matches": (
        "assertions:\n"
        "  - param_matches:\n"
        "      tool: create_issue\n"
        "      param: title\n"
        "      contains: bug"
    ),
    "param_schema_valid": (
        "assertions:\n"
        "  - param_schema_valid:\n"
        "      tool: create_issue\n"
        "      schema:\n"
        "        type: object\n"
        "        required: [title]\n"
        "        properties:\n"
        "          title: {type: string}"
    ),
    "tool_order": "assertions:\n  - tool_order: [get_info, create_issue]",
    "trajectory_matches": (
        "assertions:\n  - trajectory_matches: [search, filter, create_issue]"
    ),
    "completes_within_s": "assertions:\n  - completes_within_s: 10",
    "output_contains": 'assertions:\n  - output_contains: "Successfully created"',
    "output_matches": 'assertions:\n  - output_matches: "issue #\\\\d+ created"',
    "no_errors": "assertions:\n  - no_errors: true",
    "error_handled": 'assertions:\n  - error_handled: "ResourceNotFound"',
    "metric_above": (
        "assertions:\n  - metric_above:\n      metric: tool_efficiency\n      threshold: 0.8"
    ),
    "metric_below": (
        "assertions:\n  - metric_below:\n      metric: redundancy\n      threshold: 0.2"
    ),
    "all_of": (
        "assertions:\n"
        "  - all_of:\n"
        "      - tool_called: create_issue\n"
        "      - max_tool_calls: 5"
    ),
    "any_of": (
        "assertions:\n"
        "  - any_of:\n"
        "      - tool_called: create_issue\n"
        "      - output_contains: created"
    ),
    "none_of": (
        "assertions:\n"
        "  - none_of:\n"
        "      - tool_called: delete_all\n"
        "      - output_contains: ERROR"
    ),
    "weighted_score": (
        "assertions:\n"
        "  - weighted_score:\n"
        "      threshold: 0.75\n"
        "      weights:\n"
        "        tool_efficiency: 0.3\n"
        "        redundancy: 0.2\n"
        "        error_recovery_rate: 0.5"
    ),
}

_METRIC_EXAMPLES: dict[str, str] = {
    "tool_efficiency": (
        "# Python\n"
        "from mcptest.metrics import tool_efficiency\n"
        "result = tool_efficiency().compute(trace)\n"
        "print(result.score)  # 0.0–1.0\n\n"
        "# YAML assertion gate\n"
        "assertions:\n"
        "  - metric_above: {metric: tool_efficiency, threshold: 0.8}"
    ),
    "redundancy": (
        "assertions:\n"
        "  - metric_above: {metric: redundancy, threshold: 0.9}"
    ),
    "error_recovery_rate": (
        "assertions:\n"
        "  - metric_above: {metric: error_recovery_rate, threshold: 0.75}"
    ),
    "trajectory_similarity": (
        "# Requires a reference trace\n"
        "from mcptest.metrics import trajectory_similarity\n"
        "result = trajectory_similarity().compute(trace, reference=baseline_trace)"
    ),
    "schema_compliance": (
        "assertions:\n"
        "  - metric_above: {metric: schema_compliance, threshold: 1.0}"
    ),
    "tool_coverage": (
        "assertions:\n"
        "  - metric_above: {metric: tool_coverage, threshold: 0.5}"
    ),
    "stability": (
        "# Computed automatically when retry > 1\n"
        "mcptest run tests/ --retry 5"
    ),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a GitHub-Flavoured Markdown table."""
    sep = " | ".join("---" for _ in headers)
    header_line = " | ".join(headers)
    lines = [f"| {header_line} |", f"| {sep} |"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def _code_block(code: str, lang: str = "yaml") -> str:
    return f"```{lang}\n{code}\n```"


def _severity_badge(severity: str) -> str:
    """Return a text badge for a conformance severity level."""
    badges = {"MUST": "**MUST**", "SHOULD": "*SHOULD*", "MAY": "MAY"}
    return badges.get(severity, severity)


# ---------------------------------------------------------------------------
# Assertion reference
# ---------------------------------------------------------------------------

# Combinators have a distinct header in the generated docs
_COMBINATOR_KEYS = {"all_of", "any_of", "none_of", "weighted_score"}


def generate_assertion_reference(entries: list[dict[str, Any]]) -> str:
    """Generate the full assertions reference page as Markdown."""
    lines: list[str] = [
        "# Assertions Reference",
        "",
        (
            "Assertions verify specific behaviors of your MCP agent after a trace is "
            "recorded.  Each assertion maps to a single-key YAML entry under "
            "`assertions:` in your test file."
        ),
        "",
    ]

    # --- quick-reference table ---
    core = [e for e in entries if e["yaml_key"] not in _COMBINATOR_KEYS]
    combinators = [e for e in entries if e["yaml_key"] in _COMBINATOR_KEYS]

    lines += [
        "## Quick Reference",
        "",
        _md_table(
            ["Assertion", "Description"],
            [[f"`{e['yaml_key']}`", e["short_doc"]] for e in core],
        ),
        "",
        "## Combinators",
        "",
        _md_table(
            ["Assertion", "Description"],
            [[f"`{e['yaml_key']}`", e["short_doc"]] for e in combinators],
        ),
        "",
    ]

    # --- per-assertion detail sections ---
    lines += ["## Core Assertions", ""]
    for entry in core:
        lines += _assertion_section(entry)

    lines += ["## Boolean Combinators", ""]
    for entry in combinators:
        lines += _assertion_section(entry)

    return "\n".join(lines)


def _assertion_section(entry: dict[str, Any]) -> list[str]:
    key = entry["yaml_key"]
    lines: list[str] = [
        f"### `{key}`",
        "",
        entry["full_doc"] or entry["short_doc"],
        "",
    ]

    example = _ASSERTION_EXAMPLES.get(key)
    if example:
        lines += ["**YAML Example:**", "", _code_block(example), ""]

    fields = [f for f in entry["fields"] if not f["name"].startswith("_")]
    if fields:
        lines += [
            "**Parameters:**",
            "",
            _md_table(
                ["Parameter", "Type", "Required", "Default"],
                [
                    [
                        f"`{f['name']}`",
                        f"`{f['type']}`",
                        "Yes" if f["required"] else "No",
                        "—" if f["default"] is None else f"`{f['default']}`",
                    ]
                    for f in fields
                ],
            ),
            "",
        ]
    else:
        lines += ["*No parameters — use `true` as the value in YAML.*", ""]

    lines.append("---")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Metric reference
# ---------------------------------------------------------------------------


def generate_metric_reference(entries: list[dict[str, Any]]) -> str:
    """Generate the full metrics reference page as Markdown."""
    lines: list[str] = [
        "# Metrics Reference",
        "",
        (
            "Metrics produce a continuous quality score between **0.0** (worst) and "
            "**1.0** (best) for any recorded trace.  Unlike assertions (binary "
            "pass/fail), metrics give a nuanced quality signal useful for agent "
            "comparison and regression tracking."
        ),
        "",
        "## Quick Reference",
        "",
        _md_table(
            ["Name", "Label", "Description"],
            [
                [f"`{e['name']}`", e["label"], e["short_doc"]]
                for e in entries
            ],
        ),
        "",
        "## Metric Details",
        "",
    ]

    for entry in entries:
        lines += _metric_section(entry)

    return "\n".join(lines)


def _metric_section(entry: dict[str, Any]) -> list[str]:
    name = entry["name"]
    lines: list[str] = [
        f"### `{name}` — {entry['label']}",
        "",
        entry["full_doc"] or entry["short_doc"],
        "",
    ]

    example = _METRIC_EXAMPLES.get(name)
    if example:
        lines += ["**Example:**", "", _code_block(example), ""]

    lines += [
        "**Score interpretation:** 0.0 = worst, 1.0 = best",
        "",
        "---",
        "",
    ]
    return lines


# ---------------------------------------------------------------------------
# Conformance check reference
# ---------------------------------------------------------------------------


def generate_check_reference(entries: list[dict[str, Any]]) -> str:
    """Generate the full conformance checks reference page as Markdown."""
    lines: list[str] = [
        "# Conformance Checks Reference",
        "",
        (
            "Conformance checks verify that an MCP server correctly implements the "
            "protocol.  Each check is tagged with an RFC 2119 severity level: "
            "**MUST** (mandatory), *SHOULD* (strongly recommended), or MAY "
            "(optional)."
        ),
        "",
        "## All Checks",
        "",
        _md_table(
            ["ID", "Section", "Severity", "Description"],
            [
                [
                    f"`{e['id']}`",
                    e["section"],
                    _severity_badge(e["severity"]),
                    e["name"],
                ]
                for e in entries
            ],
        ),
        "",
    ]

    # Group by section
    sections: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        sections.setdefault(entry["section"], []).append(entry)

    for section_name, section_entries in sections.items():
        title = section_name.replace("_", " ").title()
        lines += [f"## {title}", ""]
        for entry in section_entries:
            lines += _check_section(entry)

    return "\n".join(lines)


def _check_section(entry: dict[str, Any]) -> list[str]:
    lines: list[str] = [
        f"### {entry['id']} — {entry['name']}",
        "",
        f"**Severity:** {_severity_badge(entry['severity'])}  ",
        f"**Section:** `{entry['section']}`",
        "",
    ]
    doc = entry["full_doc"] or entry["short_doc"]
    if doc:
        lines += [doc, ""]
    lines += ["---", ""]
    return lines


# ---------------------------------------------------------------------------
# CLI reference
# ---------------------------------------------------------------------------


def generate_cli_reference(entries: list[dict[str, Any]]) -> str:
    """Generate the full CLI reference page as Markdown."""
    lines: list[str] = [
        "# CLI Reference",
        "",
        "All mcptest commands are sub-commands of the `mcptest` CLI.",
        "",
        "## Commands",
        "",
        _md_table(
            ["Command", "Description"],
            [[f"`mcptest {e['name']}`", (e["help"] or "").split("\n")[0]] for e in entries],
        ),
        "",
    ]

    for entry in entries:
        lines += _cli_section(entry)

    return "\n".join(lines)


def _cli_section(entry: dict[str, Any]) -> list[str]:
    name = entry["name"]
    lines: list[str] = [
        f"## `mcptest {name}`",
        "",
        entry["help"] or f"Run `mcptest {name} --help` for details.",
        "",
    ]

    params = [p for p in entry["params"] if p["name"] != "help"]
    if params:
        rows = []
        for p in params:
            opts = ", ".join(f"`{o}`" for o in p["opts"])
            type_name = p["type"] if p["type"] != "BOOL" else "flag"
            required = "Yes" if p["required"] else "No"
            default = "—" if p["default"] is None else f"`{p['default']}`"
            rows.append([opts, type_name, required, default, p["help"]])

        lines += [
            "**Options:**",
            "",
            _md_table(["Option", "Type", "Required", "Default", "Description"], rows),
            "",
        ]

    lines += ["---", ""]
    return lines


# ---------------------------------------------------------------------------
# Full reference bundle
# ---------------------------------------------------------------------------


def generate_full_reference() -> dict[str, str]:
    """Generate all four reference pages and return them as a dict.

    Keys: ``"assertions.md"``, ``"metrics.md"``, ``"checks.md"``, ``"cli.md"``
    """
    from mcptest.cli.main import main as cli_main
    from mcptest.docs.extractors import (
        extract_assertions,
        extract_checks,
        extract_cli_commands,
        extract_metrics,
    )

    return {
        "assertions.md": generate_assertion_reference(extract_assertions()),
        "metrics.md": generate_metric_reference(extract_metrics()),
        "checks.md": generate_check_reference(extract_checks()),
        "cli.md": generate_cli_reference(extract_cli_commands(cli_main)),
    }
