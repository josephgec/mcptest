"""Documentation engine for mcptest.

Provides auto-generated reference documentation extracted from live registries,
Rich-formatted terminal help via ``mcptest explain``, and a MkDocs site builder
via ``mcptest docs build``.

Public API::

    from mcptest.docs import extract_assertions, extract_metrics, extract_checks
    from mcptest.docs import generate_reference, explain, list_all, build_site
"""

from __future__ import annotations

from mcptest.docs.extractors import (
    extract_assertions,
    extract_checks,
    extract_cli_commands,
    extract_metrics,
)
from mcptest.docs.generators import generate_full_reference
from mcptest.docs.terminal import explain, list_all

__all__ = [
    "build_site",
    "explain",
    "extract_assertions",
    "extract_checks",
    "extract_cli_commands",
    "extract_metrics",
    "generate_full_reference",
    "list_all",
]


def build_site(output_dir: object = None) -> list:
    """Generate a full MkDocs site into ``output_dir``."""
    from mcptest.docs.site import build_site as _build_site

    return _build_site(output_dir)
