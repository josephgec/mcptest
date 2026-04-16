"""mcptest MCP server — expose the mcptest toolbox over the MCP stdio protocol.

Run with::

    python -m mcptest.mcp_server          # stdio (default)
    mcptest-mcp-server                    # console-script entry point

Any MCP client (Claude Code, Cursor, …) can then discover and call the
10 mcptest tools natively without leaving the chat interface.
"""

from __future__ import annotations

from mcptest.mcp_server.server import build_server, run_stdio

__all__ = ["build_server", "run_stdio"]
