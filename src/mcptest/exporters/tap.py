"""TAP (Test Anything Protocol) v14 exporter for mcptest results.

Produces TAP v14 output with YAML diagnostic blocks for failed cases.
Understood by every TAP consumer in the Node.js / Perl / Ruby ecosystem
and most modern CI systems.
"""

from __future__ import annotations

from typing import Any

import yaml

from mcptest.exporters.base import Exporter, register_exporter


@register_exporter("tap")
class TAPExporter(Exporter):
    """Exports a list of CaseResult objects to TAP version 14."""

    def export(self, results: list[Any], *, suite_name: str = "mcptest") -> str:
        """Return a TAP v14 string for *results*."""
        lines: list[str] = [
            "TAP version 14",
            f"1..{len(results)}",
        ]

        for i, r in enumerate(results, start=1):
            test_id = f"{r.suite_name}::{r.case_name}"
            if r.passed:
                lines.append(
                    f"ok {i} - {test_id} # time={r.trace.duration_s:.3f}s"
                )
            else:
                lines.append(f"not ok {i} - {test_id}")
                diag = _build_diagnostic(r)
                yaml_str = yaml.dump(
                    diag,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                ).rstrip()
                lines.append("  ---")
                for line in yaml_str.splitlines():
                    lines.append(f"  {line}")
                lines.append("  ...")

        return "\n".join(lines) + "\n"


def _build_diagnostic(r: Any) -> dict[str, Any]:
    """Build the YAML diagnostic dict for a failed CaseResult."""
    diag: dict[str, Any] = {}

    if r.error is not None:
        diag["message"] = r.error
        diag["severity"] = "error"
    elif not r.trace.succeeded:
        diag["message"] = (
            r.trace.agent_error or f"exit_code={r.trace.exit_code}"
        )
        diag["severity"] = "error"
    else:
        failed = [a for a in r.assertion_results if not a.passed]
        if failed:
            diag["message"] = "; ".join(a.message for a in failed)
        diag["severity"] = "fail"

    diag["duration_s"] = r.trace.duration_s
    if r.trace.trace_id:
        diag["trace_id"] = r.trace.trace_id
    if r.metrics:
        diag["metrics"] = {m.name: round(m.score, 4) for m in r.metrics}

    return diag
