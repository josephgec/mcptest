"""Coverage analysis engine for MCP fixture surface area.

Given a set of fixtures and a collection of traces (from one or more test
runs), this module computes *fixture surface area coverage*: which tool
responses were actually exercised, which error scenarios were injected, and
how much of the declared fixture surface the test suite covers overall.

The analysis reuses ``match_response()`` from the mock server so that
response-hit tracking is always in sync with actual mock-server dispatch
logic — no separate tracking metadata required in the trace format.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcptest.fixtures.models import Fixture
    from mcptest.runner.trace import Trace
    from mcptest.testspec.models import TestCase


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponseCoverageItem:
    """Coverage data for one response entry in a fixture tool."""

    index: int
    """0-based index of this response in the tool's ``responses`` list."""

    hit_count: int
    """Number of times this response was triggered across all traces."""

    is_default: bool
    """True when the response is marked ``default: true``."""

    match: dict[str, Any] | None
    """Exact-match conditions, or ``None`` if the response has no ``match:`` block."""

    match_regex: dict[str, str] | None
    """Regex-match conditions, or ``None`` if no ``match_regex:`` block."""

    hit: bool
    """Shorthand: ``hit_count > 0``."""


@dataclass(frozen=True)
class ToolCoverageItem:
    """Coverage data for one tool declared in a fixture."""

    name: str
    """Tool name as declared in the fixture."""

    call_count: int
    """Total invocations across all traces."""

    responses_total: int
    """Number of response entries declared for this tool."""

    responses_hit: int
    """Number of response entries triggered at least once."""

    response_details: tuple[ResponseCoverageItem, ...]
    """Per-response breakdown."""


@dataclass(frozen=True)
class ErrorCoverageItem:
    """Coverage data for one named error scenario in a fixture."""

    name: str
    """Error name as declared in the fixture's ``errors:`` list."""

    tool: str | None
    """Tool this error is scoped to, or ``None`` if it applies to any tool."""

    injected: bool
    """True when at least one test case used ``inject_error: <name>``."""

    injection_count: int
    """Number of test cases that injected this error."""


@dataclass(frozen=True)
class CoverageReport:
    """Aggregate fixture coverage report.

    Scores:
    - *tool score* = tools_used / tools_total
    - *response score* = responses_hit / responses_total
    - *error score* = errors_injected / errors_total
    - *overall_score* = weighted average (40 % tool, 40 % response, 20 % error),
      with weights redistributed if a category has zero items.
    """

    tools_total: int
    tools_used: int
    tool_details: tuple[ToolCoverageItem, ...]

    responses_total: int
    responses_hit: int

    errors_total: int
    errors_injected: int
    error_details: tuple[ErrorCoverageItem, ...]

    overall_score: float
    """Weighted overall coverage score in [0, 1]."""

    uncovered_summary: tuple[str, ...]
    """Plain-English suggestions for improving coverage."""

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "tools_total": self.tools_total,
            "tools_used": self.tools_used,
            "tool_score": self.tools_used / self.tools_total if self.tools_total else 1.0,
            "responses_total": self.responses_total,
            "responses_hit": self.responses_hit,
            "response_score": (
                self.responses_hit / self.responses_total
                if self.responses_total
                else 1.0
            ),
            "errors_total": self.errors_total,
            "errors_injected": self.errors_injected,
            "error_score": (
                self.errors_injected / self.errors_total if self.errors_total else 1.0
            ),
            "overall_score": self.overall_score,
            "tool_details": [
                {
                    "name": t.name,
                    "call_count": t.call_count,
                    "responses_total": t.responses_total,
                    "responses_hit": t.responses_hit,
                    "responses": [
                        {
                            "index": r.index,
                            "hit_count": r.hit_count,
                            "is_default": r.is_default,
                            "match": r.match,
                            "match_regex": r.match_regex,
                            "hit": r.hit,
                        }
                        for r in t.response_details
                    ],
                }
                for t in self.tool_details
            ],
            "error_details": [
                {
                    "name": e.name,
                    "tool": e.tool,
                    "injected": e.injected,
                    "injection_count": e.injection_count,
                }
                for e in self.error_details
            ],
            "uncovered_summary": list(self.uncovered_summary),
        }

    def to_text(self) -> str:
        """Return a human-readable plain-text summary (no ANSI codes).

        Designed to be diffable and testable without a real terminal.
        """
        lines: list[str] = []

        # --- Tool / response table ---
        w_name, w_calls, w_resp, w_hit, w_score = 22, 7, 11, 5, 7
        header = (
            f"{'Tool':<{w_name}} {'Calls':>{w_calls}} {'Responses':>{w_resp}}"
            f" {'Hit':>{w_hit}} {'Score':>{w_score}}"
        )
        sep = "-" * len(header)
        lines += ["Fixture Coverage", sep, header, sep]

        for t in self.tool_details:
            score = t.responses_hit / t.responses_total if t.responses_total else 1.0
            mark = "+" if t.call_count > 0 else "-"
            lines.append(
                f"{t.name:<{w_name}} {mark + str(t.call_count):>{w_calls}}"
                f" {t.responses_total:>{w_resp}} {t.responses_hit:>{w_hit}}"
                f" {score:>{w_score}.0%}"
            )
        lines.append(sep)

        # --- Error table ---
        if self.error_details:
            lines += ["", "Error Coverage"]
            e_sep = "-" * 54
            lines += [e_sep, f"{'Error':<22} {'Tool Scope':<20} {'Injected':>9}", e_sep]
            for e in self.error_details:
                mark = "+" if e.injected else "-"
                lines.append(
                    f"{e.name:<22} {(e.tool or 'any'):<20} {mark:>9}"
                )
            lines.append(e_sep)

        # --- Overall score ---
        lines += ["", f"Overall coverage score: {self.overall_score:.1%}"]

        # --- Suggestions ---
        if self.uncovered_summary:
            lines += ["", "Suggestions:"]
            for s in self.uncovered_summary:
                lines.append(f"  * {s}")

        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Core analysis function
# ---------------------------------------------------------------------------


def analyze_coverage(
    fixtures: list[Fixture],
    traces: list[Trace],
    *,
    test_cases: list[TestCase] | None = None,
) -> CoverageReport:
    """Analyse fixture surface area coverage from *traces*.

    Parameters
    ----------
    fixtures:
        Parsed fixture objects whose tool/error declarations define the
        coverage surface.
    traces:
        Agent-run traces to analyse.  Tool calls in each trace are matched
        against fixture responses using the same ``match_response()`` logic
        the mock server uses at runtime.
    test_cases:
        Optional list of test case specs.  When provided, ``inject_error``
        fields are used to populate error coverage counts.

    Returns
    -------
    CoverageReport
        Immutable report with per-tool, per-response, and per-error coverage
        data plus weighted overall score.
    """
    from mcptest.mock_server.matcher import NoMatchError, match_response

    # -----------------------------------------------------------------------
    # 1. Build tool map (first fixture wins for duplicate tool names)
    # -----------------------------------------------------------------------
    from mcptest.fixtures.models import ToolSpec  # noqa: F401

    tool_map: dict[str, ToolSpec] = {}
    for fixture in fixtures:
        for tool in fixture.tools:
            if tool.name not in tool_map:
                tool_map[tool.name] = tool

    # -----------------------------------------------------------------------
    # 2. Initialise per-tool hit counters
    # -----------------------------------------------------------------------
    # response_hits[tool_name][response_index] = hit count
    response_hits: dict[str, list[int]] = {
        name: [0] * len(spec.responses) for name, spec in tool_map.items()
    }
    call_counts: dict[str, int] = {name: 0 for name in tool_map}

    # -----------------------------------------------------------------------
    # 3. Walk traces, re-run matcher to determine which response was hit
    # -----------------------------------------------------------------------
    for trace in traces:
        for call in trace.tool_calls:
            if call.tool not in tool_map:
                continue
            call_counts[call.tool] += 1
            spec = tool_map[call.tool]
            try:
                matched = match_response(spec.responses, call.arguments)
            except NoMatchError:
                continue
            # Find the index of the matched response using identity check.
            # match_response() returns the actual list element, not a copy.
            for i, r in enumerate(spec.responses):
                if r is matched:
                    response_hits[call.tool][i] += 1
                    break

    # -----------------------------------------------------------------------
    # 4. Build ToolCoverageItem list
    # -----------------------------------------------------------------------
    tool_details: list[ToolCoverageItem] = []
    for name, spec in tool_map.items():
        hits = response_hits[name]
        response_items = tuple(
            ResponseCoverageItem(
                index=i,
                hit_count=hits[i],
                is_default=spec.responses[i].default,
                match=spec.responses[i].match,
                match_regex=spec.responses[i].match_regex,
                hit=hits[i] > 0,
            )
            for i in range(len(spec.responses))
        )
        tool_details.append(
            ToolCoverageItem(
                name=name,
                call_count=call_counts[name],
                responses_total=len(spec.responses),
                responses_hit=sum(1 for h in hits if h > 0),
                response_details=response_items,
            )
        )

    # -----------------------------------------------------------------------
    # 5. Error injection tracking
    # -----------------------------------------------------------------------
    all_errors = []
    seen_error_names: set[str] = set()
    for fixture in fixtures:
        for err in fixture.errors:
            if err.name not in seen_error_names:
                seen_error_names.add(err.name)
                all_errors.append(err)

    injection_counts: dict[str, int] = {e.name: 0 for e in all_errors}
    if test_cases:
        for case in test_cases:
            if case.inject_error and case.inject_error in injection_counts:
                injection_counts[case.inject_error] += 1

    error_details: list[ErrorCoverageItem] = [
        ErrorCoverageItem(
            name=e.name,
            tool=e.tool,
            injected=injection_counts[e.name] > 0,
            injection_count=injection_counts[e.name],
        )
        for e in all_errors
    ]

    # -----------------------------------------------------------------------
    # 6. Compute aggregate scores
    # -----------------------------------------------------------------------
    tools_total = len(tool_map)
    tools_used = sum(1 for c in call_counts.values() if c > 0)

    responses_total = sum(t.responses_total for t in tool_details)
    responses_hit = sum(t.responses_hit for t in tool_details)

    errors_total = len(all_errors)
    errors_injected = sum(1 for e in error_details if e.injected)

    tool_score = tools_used / tools_total if tools_total else 1.0
    response_score = responses_hit / responses_total if responses_total else 1.0
    error_score = errors_injected / errors_total if errors_total else 1.0

    # Weighted 40 / 40 / 20 — redistribute if a category is empty
    w_tool = 0.4 if tools_total > 0 else 0.0
    w_resp = 0.4 if responses_total > 0 else 0.0
    w_err = 0.2 if errors_total > 0 else 0.0
    total_weight = w_tool + w_resp + w_err

    if total_weight == 0.0:
        overall_score = 1.0
    else:
        overall_score = (
            w_tool * tool_score + w_resp * response_score + w_err * error_score
        ) / total_weight

    overall_score = round(overall_score, 6)

    # -----------------------------------------------------------------------
    # 7. Build uncovered suggestions
    # -----------------------------------------------------------------------
    suggestions: list[str] = []
    for t in tool_details:
        if t.call_count == 0:
            suggestions.append(
                f"Tool '{t.name}' was never called — add a test that uses it"
            )
        else:
            for r in t.response_details:
                if not r.hit:
                    desc = _describe_response(r)
                    suggestions.append(
                        f"Tool '{t.name}' {desc} was never triggered"
                        f" — add a test case that causes this response"
                    )
    for e in error_details:
        if not e.injected:
            scope = f" (tool: {e.tool})" if e.tool else ""
            suggestions.append(
                f"Error '{e.name}'{scope} was never injected"
                f" — add a test case with inject_error: {e.name}"
            )

    return CoverageReport(
        tools_total=tools_total,
        tools_used=tools_used,
        tool_details=tuple(tool_details),
        responses_total=responses_total,
        responses_hit=responses_hit,
        errors_total=errors_total,
        errors_injected=errors_injected,
        error_details=tuple(error_details),
        overall_score=overall_score,
        uncovered_summary=tuple(suggestions),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _describe_response(r: ResponseCoverageItem) -> str:
    """Short description of a response entry for use in suggestions."""
    if r.is_default:
        return f"response #{r.index} (default)"
    if r.match:
        return f"response #{r.index} (match: {r.match})"
    if r.match_regex:
        return f"response #{r.index} (match_regex: {r.match_regex})"
    return f"response #{r.index}"
