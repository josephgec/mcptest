"""mcptest — pytest for MCP agents."""

from __future__ import annotations

__version__ = "0.1.0"

from mcptest.fixtures import (
    ErrorSpec,
    Fixture,
    FixtureLoadError,
    Response,
    ResourceSpec,
    ServerSpec,
    ToolSpec,
    load_fixture,
    load_fixtures,
)

__all__ = [
    "__version__",
    "ErrorSpec",
    "Fixture",
    "FixtureLoadError",
    "Response",
    "ResourceSpec",
    "ServerSpec",
    "ToolSpec",
    "load_fixture",
    "load_fixtures",
]
