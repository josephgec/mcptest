"""Top-level `mcptest` CLI group."""

from __future__ import annotations

import click

from mcptest import __version__
from mcptest.cli.commands import (
    diff_command,
    init_command,
    record_command,
    run_command,
    snapshot_command,
    validate_command,
)


@click.group(
    help=(
        "mcptest — a testing framework for MCP agents.\n\n"
        "Run `mcptest init` to scaffold a new project, then `mcptest run` to "
        "execute your tests."
    )
)
@click.version_option(__version__, prog_name="mcptest")
def main() -> None:  # pragma: no cover - click entry
    pass


main.add_command(init_command, name="init")
main.add_command(run_command, name="run")
main.add_command(validate_command, name="validate")
main.add_command(record_command, name="record")
main.add_command(snapshot_command, name="snapshot")
main.add_command(diff_command, name="diff")


if __name__ == "__main__":  # pragma: no cover
    main()
