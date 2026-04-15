"""mcptest capture — live server discovery, sampling, and fixture/test generation.

Point mcptest at any running MCP server and it will auto-discover tools,
sample responses, and produce both fixture YAML and test spec YAML — the
"record and replay" paradigm applied to MCP.

Public API
----------
ServerDiscovery        Connect to a server and enumerate its capabilities.
DiscoveryResult        Dataclass returned by ServerDiscovery.discover().
ToolSampler            Generate sample args and execute them against a live server.
SampledTool            Dataclass holding a tool's schema + (args, response) pairs.
FixtureGenerator       Convert discovery + samples into fixture YAML.
capture_server         End-to-end orchestration: connect → discover → sample → write.
CaptureResult          Dataclass returned by capture_server().
"""

from __future__ import annotations

from mcptest.capture.discovery import DiscoveryResult, ServerDiscovery
from mcptest.capture.fixture_gen import FixtureGenerator
from mcptest.capture.runner import CaptureResult, capture_server
from mcptest.capture.sampler import SampledTool, ToolSampler

__all__ = [
    "CaptureResult",
    "DiscoveryResult",
    "FixtureGenerator",
    "SampledTool",
    "ServerDiscovery",
    "ToolSampler",
    "capture_server",
]
