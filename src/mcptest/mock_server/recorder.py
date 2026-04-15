"""Call recording for the mock MCP server.

Every tool invocation observed by a `MockMCPServer` is logged as a
`RecordedCall`. The test runner consumes these to build the trajectory trace
that assertions are evaluated against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RecordedCall:
    """One observed tool invocation on the mock server.

    Attributes:
        tool: The tool name that was invoked.
        arguments: The arguments dict passed in.
        result: The payload we returned for normal responses (None for errors).
        error: Human-readable error message if this call produced an error
            response; None otherwise.
        error_code: JSON-RPC-style numeric error code if an error occurred.
        latency_ms: Actual observed latency including any simulated delay.
        server_name: Which fixture server produced this call.
        index: Sequence number within a single test run, assigned by the
            recorder on append.
    """

    tool: str
    arguments: dict[str, Any]
    result: Any | None = None
    error: str | None = None
    error_code: int | None = None
    latency_ms: float = 0.0
    server_name: str = ""
    index: int = 0

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "server": self.server_name,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
            "error_code": self.error_code,
            "latency_ms": self.latency_ms,
        }


@dataclass
class CallLog:
    """Append-only log of observed tool calls shared between mocks and runner."""

    calls: list[RecordedCall] = field(default_factory=list)

    def append(self, call: RecordedCall) -> RecordedCall:
        call.index = len(self.calls)
        self.calls.append(call)
        return call

    def clear(self) -> None:
        self.calls.clear()

    def __len__(self) -> int:
        return len(self.calls)

    def __iter__(self):
        return iter(self.calls)
