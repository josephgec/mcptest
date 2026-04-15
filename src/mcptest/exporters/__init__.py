"""CI output format exporters for mcptest.

Importing this package registers all built-in exporters (JUnit XML, TAP v14)
into the ``EXPORTERS`` registry so ``get_exporter("junit")`` etc. work.
"""

from __future__ import annotations

from mcptest.exporters.base import EXPORTERS, Exporter, get_exporter, register_exporter
from mcptest.exporters.junit import JUnitExporter
from mcptest.exporters.tap import TAPExporter

__all__ = [
    "EXPORTERS",
    "Exporter",
    "JUnitExporter",
    "TAPExporter",
    "get_exporter",
    "register_exporter",
]
