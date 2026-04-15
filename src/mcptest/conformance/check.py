"""Base framework for MCP server conformance checks.

Each conformance check is an async function decorated with
``@conformance_check``, which registers it in the global ``CHECKS`` list.
Checks are identified by a structured ID (e.g. "INIT-001"), grouped into
sections, and tagged with an RFC 2119 severity level (MUST / SHOULD / MAY).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


class Severity(str, enum.Enum):
    """RFC 2119 conformance severity levels."""

    MUST = "MUST"
    SHOULD = "SHOULD"
    MAY = "MAY"


# ---------------------------------------------------------------------------
# CheckOutcome — the raw result of running one check function
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckOutcome:
    """The result produced by a single conformance check function."""

    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure details is always a plain dict (not a subclass) so tests
        # can compare with == without worrying about type identity.
        object.__setattr__(self, "details", dict(self.details))


# ---------------------------------------------------------------------------
# ConformanceCheck — a registered check descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConformanceCheck:
    """Descriptor for one registered conformance check."""

    id: str
    section: str
    name: str
    severity: Severity
    fn: Callable  # async (ServerUnderTest) -> CheckOutcome


# ---------------------------------------------------------------------------
# ConformanceResult — outcome + descriptor + timing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConformanceResult:
    """The full result of executing a ConformanceCheck against a server."""

    check: ConformanceCheck
    passed: bool
    message: str
    details: dict[str, Any]
    duration_ms: float
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.check.id,
            "section": self.check.section,
            "name": self.check.name,
            "severity": self.check.severity.value,
            "passed": self.passed,
            "skipped": self.skipped,
            "message": self.message,
            "details": self.details,
            "duration_ms": round(self.duration_ms, 2),
        }


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

CHECKS: list[ConformanceCheck] = []


def conformance_check(
    id: str,
    section: str,
    name: str,
    severity: Severity,
) -> Callable:
    """Decorator that registers an async check function in ``CHECKS``.

    Usage::

        @conformance_check("INIT-001", "initialization", "Server name present", Severity.MUST)
        async def check_init_name(server: ServerUnderTest) -> CheckOutcome:
            ...
    """

    def decorator(fn: Callable) -> Callable:
        CHECKS.append(
            ConformanceCheck(id=id, section=section, name=name, severity=severity, fn=fn)
        )
        return fn

    return decorator
