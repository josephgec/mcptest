"""Top-level `mcptest` CLI group."""

from __future__ import annotations

import click

from mcptest import __version__
from mcptest.cli.commands import (
    diff_command,
    init_command,
    install_pack_command,
    list_packs_command,
    record_command,
    run_command,
    snapshot_command,
    validate_command,
)
from mcptest.cli.github import badge_command, github_comment_command


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
main.add_command(github_comment_command, name="github-comment")
main.add_command(badge_command, name="badge")
main.add_command(install_pack_command, name="install-pack")
main.add_command(list_packs_command, name="list-packs")


if __name__ == "__main__":  # pragma: no cover
    main()
