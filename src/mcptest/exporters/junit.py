"""JUnit XML exporter for mcptest results.

Maps mcptest CaseResult objects to standard JUnit XML schema understood
by Jenkins, GitLab CI, Azure DevOps, CircleCI, Buildkite, and GitHub Actions.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from typing import Any

from mcptest.exporters.base import Exporter, register_exporter


@register_exporter("junit")
class JUnitExporter(Exporter):
    """Exports a list of CaseResult objects to JUnit XML."""

    def export(self, results: list[Any], *, suite_name: str = "mcptest") -> str:
        """Return a JUnit XML string for *results*."""
        # Group results by suite_name so each suite becomes a <testsuite>.
        suites: dict[str, list[Any]] = {}
        for r in results:
            suites.setdefault(r.suite_name, []).append(r)

        # Failures = assertion failures (error is None, trace succeeded).
        # Errors   = runner errors OR agent exit failures.
        total_failures = sum(
            1 for r in results
            if not r.passed and r.error is None and r.trace.succeeded
        )
        total_errors = sum(
            1 for r in results
            if r.error is not None or not r.trace.succeeded
        )
        total_time = sum(r.trace.duration_s for r in results)

        root = ET.Element("testsuites")
        root.set("name", suite_name)
        root.set("tests", str(len(results)))
        root.set("failures", str(total_failures))
        root.set("errors", str(total_errors))
        root.set("time", f"{total_time:.3f}")

        for suite_nm, suite_results in suites.items():
            s_failures = sum(
                1 for r in suite_results
                if not r.passed and r.error is None and r.trace.succeeded
            )
            s_errors = sum(
                1 for r in suite_results
                if r.error is not None or not r.trace.succeeded
            )
            s_time = sum(r.trace.duration_s for r in suite_results)

            suite_el = ET.SubElement(root, "testsuite")
            suite_el.set("name", suite_nm)
            suite_el.set("tests", str(len(suite_results)))
            suite_el.set("failures", str(s_failures))
            suite_el.set("errors", str(s_errors))
            suite_el.set("time", f"{s_time:.3f}")

            for r in suite_results:
                tc = ET.SubElement(suite_el, "testcase")
                tc.set("classname", suite_nm)
                tc.set("name", r.case_name)
                tc.set("time", f"{r.trace.duration_s:.3f}")

                # Emit <properties> for trace_id and per-metric scores.
                if r.trace.trace_id or r.metrics:
                    props = ET.SubElement(tc, "properties")
                    if r.trace.trace_id:
                        prop = ET.SubElement(props, "property")
                        prop.set("name", "mcptest.trace_id")
                        prop.set("value", r.trace.trace_id)
                    for m in r.metrics:
                        prop = ET.SubElement(props, "property")
                        prop.set("name", f"mcptest.metric.{m.name}")
                        prop.set("value", f"{m.score:.4f}")

                # Emit retry/flakiness metadata when multi-attempt data is present.
                rr = getattr(r, "retry_result", None)
                if rr is not None and len(rr.traces) > 1:
                    tc.set("attempts", str(len(rr.traces)))
                    tc.set("pass_rate", f"{rr.pass_rate:.4f}")
                    tc.set("stability", f"{rr.stability:.4f}")

                if r.error is not None:
                    err_el = ET.SubElement(tc, "error")
                    err_el.set("message", str(r.error))
                    err_el.set("type", "RunnerError")
                    err_el.text = str(r.error)
                elif not r.trace.succeeded:
                    agent_err = (
                        r.trace.agent_error or f"exit_code={r.trace.exit_code}"
                    )
                    err_el = ET.SubElement(tc, "error")
                    err_el.set("message", agent_err)
                    err_el.set("type", "AgentError")
                    err_el.text = agent_err
                elif not r.passed:
                    # For multi-attempt runs: if some attempts passed and some
                    # failed, this is a flaky failure — emit <flakyFailure>
                    # (supported by CircleCI, GitLab CI, and Jenkins).
                    pass_count = sum(1 for v in rr.attempt_results if v) if rr is not None else 0
                    n_attempts = len(rr.traces) if rr is not None else 1
                    is_flaky = (
                        rr is not None
                        and n_attempts > 1
                        and 0 < pass_count < n_attempts
                    )
                    if is_flaky:
                        flaky_el = ET.SubElement(tc, "flakyFailure")
                        flaky_msg = (
                            f"Flaky: {pass_count}/{n_attempts} attempts passed "
                            f"(tolerance={rr.tolerance:.2f}, pass_rate={rr.pass_rate:.4f}, "
                            f"stability={rr.stability:.4f})"
                        )
                        flaky_el.set("message", flaky_msg)
                        flaky_el.set("type", "FlakyFailure")
                        flaky_el.text = flaky_msg
                    else:
                        for a in (a for a in r.assertion_results if not a.passed):
                            fail_el = ET.SubElement(tc, "failure")
                            fail_el.set("message", a.message)
                            fail_el.set("type", "AssertionFailure")
                            text_parts = [f"{a.name}: {a.message}"]
                            for k, v in a.details.items():
                                text_parts.append(f"  {k}: {v}")
                            fail_el.text = "\n".join(text_parts)

        ET.indent(root, space="  ")
        tree = ET.ElementTree(root)
        buf = io.BytesIO()
        tree.write(buf, encoding="utf-8", xml_declaration=True)
        return buf.getvalue().decode("utf-8")
