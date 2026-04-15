"""Metadata extractors for the documentation engine.

Each extractor reads from a live registry (ASSERTIONS, METRICS, CHECKS) or
a Click CLI group and returns a list of plain dicts suitable for downstream
Markdown generation or terminal rendering.  Importing this module triggers
all registration side-effects so the registries are fully populated before
extraction.
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_assertions_registered() -> None:
    """Import assertion modules to trigger @register_assertion decorators."""
    import mcptest.assertions.combinators  # noqa: F401
    import mcptest.assertions.impls  # noqa: F401


def _ensure_metrics_registered() -> None:
    """Import metrics module to trigger @register_metric decorators."""
    import mcptest.metrics.impls  # noqa: F401


def _ensure_checks_registered() -> None:
    """Import checks module to trigger @conformance_check decorators."""
    import mcptest.conformance.checks  # noqa: F401


def _field_entry(f: dataclasses.Field) -> dict[str, Any] | None:  # type: ignore[type-arg]
    """Return a documentation dict for a dataclass field, or None to skip."""
    if f.name.startswith("_"):
        return None

    has_default = (
        f.default is not dataclasses.MISSING
        or f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
    )

    default: Any = None
    if f.default is not dataclasses.MISSING:
        # Only expose clean Python primitives; sentinels/complex objects → None
        if isinstance(f.default, (bool, int, float, str, type(None))):
            default = f.default
    elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        default = "[]"

    # Annotation stored as string (from __future__ import annotations)
    type_str = str(f.type) if not isinstance(f.type, type) else f.type.__name__

    return {
        "name": f.name,
        "type": type_str,
        "required": not has_default,
        "default": default,
    }


def _short_doc(obj: Any) -> str:
    """Return the first non-empty line of an object's docstring."""
    doc = inspect.getdoc(obj) or ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _full_doc(obj: Any) -> str:
    """Return the full cleaned docstring of an object."""
    return inspect.getdoc(obj) or ""


# ---------------------------------------------------------------------------
# Public extractors
# ---------------------------------------------------------------------------


def extract_assertions() -> list[dict[str, Any]]:
    """Return structured metadata for every registered assertion.

    Each entry is a dict with keys:
        yaml_key   — the string used in YAML test files
        short_doc  — one-line description
        full_doc   — full docstring
        fields     — list of field dicts (name, type, required, default)
    """
    _ensure_assertions_registered()
    from mcptest.assertions.base import ASSERTIONS

    results: list[dict[str, Any]] = []
    for yaml_key, cls in ASSERTIONS.items():
        fields: list[dict[str, Any]] = []
        if dataclasses.is_dataclass(cls):
            for f in dataclasses.fields(cls):
                entry = _field_entry(f)
                if entry is not None:
                    fields.append(entry)

        results.append(
            {
                "yaml_key": yaml_key,
                "short_doc": _short_doc(cls),
                "full_doc": _full_doc(cls),
                "fields": fields,
            }
        )
    return results


def extract_metrics() -> list[dict[str, Any]]:
    """Return structured metadata for every registered metric.

    Each entry is a dict with keys:
        name       — the metric key used in YAML / Python
        label      — human-readable label
        short_doc  — one-line description
        full_doc   — full docstring
    """
    _ensure_metrics_registered()
    from mcptest.metrics.base import METRICS

    results: list[dict[str, Any]] = []
    for name, cls in METRICS.items():
        results.append(
            {
                "name": name,
                "label": getattr(cls, "label", name),
                "short_doc": _short_doc(cls),
                "full_doc": _full_doc(cls),
            }
        )
    return results


def extract_checks() -> list[dict[str, Any]]:
    """Return structured metadata for every registered conformance check.

    Each entry is a dict with keys:
        id         — check identifier (e.g. "INIT-001")
        section    — section name (e.g. "initialization")
        name       — human-readable check name
        severity   — "MUST", "SHOULD", or "MAY"
        short_doc  — first line of the check function's docstring
        full_doc   — full docstring of the check function
    """
    _ensure_checks_registered()
    from mcptest.conformance.check import CHECKS

    results: list[dict[str, Any]] = []
    for check in CHECKS:
        results.append(
            {
                "id": check.id,
                "section": check.section,
                "name": check.name,
                "severity": check.severity.value,
                "short_doc": _short_doc(check.fn),
                "full_doc": _full_doc(check.fn),
            }
        )
    return results


def extract_cli_commands(cli_group: Any) -> list[dict[str, Any]]:
    """Return structured metadata for every command in a Click group.

    Each entry is a dict with keys:
        name    — command name (e.g. "run")
        help    — help text
        params  — list of param dicts (name, type, required, default, help)
    """
    results: list[dict[str, Any]] = []
    commands = getattr(cli_group, "commands", {})
    for cmd_name, cmd in sorted(commands.items()):
        params: list[dict[str, Any]] = []
        for p in cmd.params:
            param_type = getattr(p.type, "name", str(type(p.type).__name__))
            params.append(
                {
                    "name": p.name,
                    "type": param_type,
                    "required": p.required,
                    "default": p.default,
                    "help": getattr(p, "help", "") or "",
                    "is_flag": getattr(p, "is_flag", False),
                    "opts": list(p.opts),
                }
            )
        results.append(
            {
                "name": cmd_name,
                "help": cmd.help or "",
                "params": params,
            }
        )
    return results
