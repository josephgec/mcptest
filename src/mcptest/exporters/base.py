"""Exporter base class and registry for CI output formats."""

from __future__ import annotations

from typing import Any


class Exporter:
    """Base class for CI output format exporters.

    Subclasses should implement ``export()`` and register themselves
    via ``@register_exporter("name")``.
    """

    def export(self, results: list[Any], *, suite_name: str = "mcptest") -> str:
        raise NotImplementedError  # pragma: no cover


EXPORTERS: dict[str, type] = {}


def register_exporter(name: str):
    """Class decorator — register an exporter under *name* in EXPORTERS."""

    def decorator(cls: type) -> type:
        EXPORTERS[name] = cls
        return cls

    return decorator


def get_exporter(name: str) -> Exporter:
    """Instantiate a registered exporter by name.

    Raises ``ValueError`` for unknown names.
    """
    if name not in EXPORTERS:
        raise ValueError(
            f"Unknown exporter: {name!r}. Available: {sorted(EXPORTERS)}"
        )
    return EXPORTERS[name]()
