"""Built-in test packs — fixtures + example tests for common MCP servers.

Five packs ship out of the box, each describing a typical tool family that
real MCP servers expose: filesystem, database, HTTP, git, and Slack.
`mcptest install-pack NAME` drops a pack's files into the caller's project
so users can start testing against realistic fixtures in a single command.

The registry is intentionally data-first: each pack is a `TestPack`
containing a bundle of file paths → file contents, so adding a new pack
requires only a new entry in `PACKS` and no changes to the CLI or loader.
"""

from __future__ import annotations

from mcptest.registry.packs import (
    PACKS,
    InstallError,
    TestPack,
    get_pack,
    install_pack,
    list_packs,
)

__all__ = [
    "PACKS",
    "InstallError",
    "TestPack",
    "get_pack",
    "install_pack",
    "list_packs",
]
