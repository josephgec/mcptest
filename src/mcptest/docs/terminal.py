"""Rich-formatted terminal help for ``mcptest explain`` and ``mcptest list``.

The public API is::

    explain("tool_called")   # → formatted string with assertion docs
    explain("tool_efficiency")  # → formatted string with metric docs
    explain("INIT-001")         # → formatted string with check docs
    list_all()               # → formatted table of all known names
"""

from __future__ import annotations

import difflib
import io
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_console(file: io.StringIO) -> Console:
    """Return a Rich Console writing to *file* with forced terminal rendering."""
    return Console(file=file, force_terminal=True, width=100, highlight=False)


def _render_to_string(renderable: Any) -> str:
    buf = io.StringIO()
    console = _make_console(buf)
    console.print(renderable)
    return buf.getvalue()


def _severity_style(severity: str) -> str:
    return {"MUST": "bold red", "SHOULD": "bold yellow", "MAY": "cyan"}.get(
        severity, "white"
    )


# ---------------------------------------------------------------------------
# Lookup helpers — build the index lazily
# ---------------------------------------------------------------------------


def _build_index() -> dict[str, dict[str, Any]]:
    """Return a flat name → entry dict covering assertions, metrics, and checks."""
    from mcptest.docs.extractors import (
        extract_assertions,
        extract_checks,
        extract_metrics,
    )

    index: dict[str, dict[str, Any]] = {}

    for entry in extract_assertions():
        key = entry["yaml_key"]
        index[key] = {"kind": "assertion", **entry}

    for entry in extract_metrics():
        key = entry["name"]
        index[key] = {"kind": "metric", **entry}

    for entry in extract_checks():
        key = entry["id"]
        index[key] = {"kind": "check", **entry}
        # also index by lowercase id
        index[key.lower()] = {"kind": "check", **entry}

    return index


# ---------------------------------------------------------------------------
# Renderables per kind
# ---------------------------------------------------------------------------


def _render_assertion(entry: dict[str, Any]) -> str:
    key = entry["yaml_key"]
    doc = entry["full_doc"] or entry["short_doc"] or "(no description)"

    buf = io.StringIO()
    console = _make_console(buf)

    title = Text()
    title.append("assertion  ", style="dim")
    title.append(key, style="bold cyan")

    console.print(Panel(doc, title=title, border_style="cyan", padding=(1, 2)))

    fields = [f for f in entry.get("fields", []) if not f["name"].startswith("_")]
    if fields:
        table = Table(
            "Parameter",
            "Type",
            "Required",
            "Default",
            title="Parameters",
            show_header=True,
            header_style="bold",
        )
        for f in fields:
            req = "[green]yes[/green]" if f["required"] else "[dim]no[/dim]"
            default = str(f["default"]) if f["default"] is not None else "—"
            table.add_row(f"[cyan]{f['name']}[/cyan]", f['type'], req, default)
        console.print(table)
        console.print()

    return buf.getvalue()


def _render_metric(entry: dict[str, Any]) -> str:
    name = entry["name"]
    label = entry["label"]
    doc = entry["full_doc"] or entry["short_doc"] or "(no description)"

    buf = io.StringIO()
    console = _make_console(buf)

    title = Text()
    title.append("metric  ", style="dim")
    title.append(name, style="bold green")
    title.append(f"  ({label})", style="dim")

    body = f"{doc}\n\n[dim]Score: 0.0 (worst) → 1.0 (best)[/dim]"
    console.print(Panel(body, title=title, border_style="green", padding=(1, 2)))

    return buf.getvalue()


def _render_check(entry: dict[str, Any]) -> str:
    check_id = entry["id"]
    name = entry["name"]
    severity = entry["severity"]
    section = entry["section"]
    doc = entry["full_doc"] or entry["short_doc"] or "(no description)"

    sev_style = _severity_style(severity)
    buf = io.StringIO()
    console = _make_console(buf)

    title = Text()
    title.append("check  ", style="dim")
    title.append(check_id, style="bold magenta")

    body = (
        f"[bold]{name}[/bold]\n\n"
        f"{doc}\n\n"
        f"Severity: [{sev_style}]{severity}[/{sev_style}]   "
        f"Section: [cyan]{section}[/cyan]"
    )
    console.print(Panel(body, title=title, border_style="magenta", padding=(1, 2)))

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def explain(name: str) -> str:
    """Return a Rich-formatted terminal string explaining *name*.

    *name* can be:
    - An assertion yaml_key: ``"tool_called"``, ``"max_tool_calls"``, …
    - A metric name: ``"tool_efficiency"``, ``"redundancy"``, …
    - A conformance check ID: ``"INIT-001"``, ``"CALL-003"``, …

    If *name* is not found, returns a helpful message listing similar names.
    """
    index = _build_index()

    # Try exact match first (case-insensitive for check IDs)
    entry = index.get(name) or index.get(name.upper()) or index.get(name.lower())

    if entry is not None:
        kind = entry["kind"]
        if kind == "assertion":
            return _render_assertion(entry)
        if kind == "metric":
            return _render_metric(entry)
        if kind == "check":
            return _render_check(entry)

    # Not found — suggest close matches
    all_names = [k for k in index if not k.startswith("_")]
    # Deduplicate check IDs (we stored both upper and lower)
    unique = sorted(set(all_names))
    close = difflib.get_close_matches(name, unique, n=5, cutoff=0.5)

    buf = io.StringIO()
    console = _make_console(buf)

    msg = Text()
    msg.append(f"No assertion, metric, or check named {name!r} found.\n\n", style="red")
    if close:
        msg.append("Did you mean one of:\n", style="bold")
        for c in close:
            msg.append(f"  {c}\n", style="cyan")
    else:
        msg.append("Run ", style="dim")
        msg.append("mcptest docs list", style="cyan")
        msg.append(" to see all available names.", style="dim")

    console.print(Panel(msg, title="Not found", border_style="red", padding=(1, 2)))
    return buf.getvalue()


def list_all() -> str:
    """Return a Rich-formatted table of every assertion, metric, and check."""
    from mcptest.docs.extractors import (
        extract_assertions,
        extract_checks,
        extract_metrics,
    )

    buf = io.StringIO()
    console = _make_console(buf)

    # Assertions table
    assertions_table = Table(
        "Name",
        "Description",
        title="Assertions",
        show_header=True,
        header_style="bold cyan",
        min_width=80,
    )
    for entry in extract_assertions():
        assertions_table.add_row(
            f"[cyan]{entry['yaml_key']}[/cyan]",
            entry["short_doc"],
        )
    console.print(assertions_table)
    console.print()

    # Metrics table
    metrics_table = Table(
        "Name",
        "Label",
        "Description",
        title="Metrics",
        show_header=True,
        header_style="bold green",
        min_width=80,
    )
    for entry in extract_metrics():
        metrics_table.add_row(
            f"[green]{entry['name']}[/green]",
            entry["label"],
            entry["short_doc"],
        )
    console.print(metrics_table)
    console.print()

    # Checks table
    checks_table = Table(
        "ID",
        "Severity",
        "Name",
        title="Conformance Checks",
        show_header=True,
        header_style="bold magenta",
        min_width=80,
    )
    for entry in extract_checks():
        sev_style = _severity_style(entry["severity"])
        checks_table.add_row(
            f"[magenta]{entry['id']}[/magenta]",
            f"[{sev_style}]{entry['severity']}[/{sev_style}]",
            entry["name"],
        )
    console.print(checks_table)

    return buf.getvalue()
