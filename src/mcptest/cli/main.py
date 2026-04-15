"""Top-level `mcptest` CLI group."""

from __future__ import annotations

import click

from mcptest import __version__
from mcptest.cli.commands import (
    capture_command,
    cloud_push_command,
    compare_command,
    config_command,
    conformance_command,
    coverage_command,
    diff_command,
    docs_command,
    explain_command,
    export_command,
    generate_command,
    init_command,
    install_pack_command,
    list_packs_command,
    metrics_command,
    record_command,
    run_command,
    scorecard_command,
    snapshot_command,
    validate_command,
    watch_command,
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
@click.pass_context
def main(ctx: click.Context) -> None:  # pragma: no cover - click entry
    ctx.ensure_object(dict)
    from mcptest.config import load_config
    from mcptest.plugins import load_plugins

    config = load_config()
    ctx.obj["config"] = config
    ctx.obj["loaded_plugins"] = load_plugins(config)


main.add_command(init_command, name="init")
main.add_command(run_command, name="run")
main.add_command(export_command, name="export")
main.add_command(validate_command, name="validate")
main.add_command(record_command, name="record")
main.add_command(snapshot_command, name="snapshot")
main.add_command(diff_command, name="diff")
main.add_command(github_comment_command, name="github-comment")
main.add_command(badge_command, name="badge")
main.add_command(install_pack_command, name="install-pack")
main.add_command(list_packs_command, name="list-packs")
main.add_command(metrics_command, name="metrics")
main.add_command(compare_command, name="compare")
main.add_command(cloud_push_command, name="cloud-push")
main.add_command(generate_command, name="generate")
main.add_command(coverage_command, name="coverage")
main.add_command(watch_command, name="watch")
main.add_command(scorecard_command, name="scorecard")
main.add_command(conformance_command, name="conformance")
main.add_command(capture_command, name="capture")
main.add_command(docs_command, name="docs")
main.add_command(explain_command, name="explain")
main.add_command(config_command, name="config")


if __name__ == "__main__":  # pragma: no cover
    main()
