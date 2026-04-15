"""ConformanceRunner — orchestrates running conformance checks against a server.

The runner iterates the global ``CHECKS`` list (optionally filtered by section
or severity), calls each check's async function with the ``ServerUnderTest``,
wraps the raw ``CheckOutcome`` into a ``ConformanceResult`` with timing, and
handles exceptions inside checks as failures rather than crashing the suite.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcptest.conformance.server import ServerUnderTest

from mcptest.conformance.check import (
    CHECKS,
    CheckOutcome,
    ConformanceCheck,
    ConformanceResult,
    Severity,
)


@dataclass
class ConformanceRunner:
    """Runs conformance checks against a ``ServerUnderTest``.

    Args:
        server: The server adapter to test.
        sections: Optional list of section names to run (e.g.
            ``["initialization", "tool_listing"]``).  ``None`` runs all sections.
        severities: Optional list of severity levels to include.  ``None`` runs
            all severities.
    """

    server: ServerUnderTest
    sections: list[str] | None = None
    severities: list[Severity] | None = None

    def _filtered_checks(self) -> list[ConformanceCheck]:
        checks = list(CHECKS)
        if self.sections is not None:
            checks = [c for c in checks if c.section in self.sections]
        if self.severities is not None:
            checks = [c for c in checks if c.severity in self.severities]
        return checks

    async def _should_skip_resources(self) -> bool:
        """Return True when the server has no resources capability."""
        try:
            caps = await self.server.get_capabilities()
            return "resources" not in caps
        except Exception:
            return True

    async def run(self) -> list[ConformanceResult]:
        """Execute all applicable checks and return the results list."""
        # Import checks to ensure all @conformance_check decorators have run.
        import mcptest.conformance.checks  # noqa: F401

        skip_resources = await self._should_skip_resources()

        results: list[ConformanceResult] = []
        for check in self._filtered_checks():
            # Resource checks are skipped when the server has no resources.
            if check.section == "resources" and skip_resources:
                results.append(
                    ConformanceResult(
                        check=check,
                        passed=True,
                        message="skipped — server has no resources capability",
                        details={},
                        duration_ms=0.0,
                        skipped=True,
                    )
                )
                continue

            start = time.monotonic()
            try:
                outcome: CheckOutcome = await check.fn(self.server)
            except Exception as exc:
                duration_ms = (time.monotonic() - start) * 1000
                results.append(
                    ConformanceResult(
                        check=check,
                        passed=False,
                        message=f"check raised an exception: {exc}",
                        details={"traceback": traceback.format_exc()},
                        duration_ms=duration_ms,
                        skipped=False,
                    )
                )
                continue

            duration_ms = (time.monotonic() - start) * 1000
            results.append(
                ConformanceResult(
                    check=check,
                    passed=outcome.passed,
                    message=outcome.message,
                    details=dict(outcome.details),
                    duration_ms=duration_ms,
                    skipped=False,
                )
            )

        return results
