"""Call recording for the mock MCP server.

Every tool invocation observed by a `MockMCPServer` is logged as a
`RecordedCall`. The test runner consumes these to build the trajectory trace
that assertions are evaluated against.

When mock servers run as child processes of an agent (the typical MCP usage
pattern), they cannot share an in-memory `CallLog` with the runner. The
`TraceFileCallLog` subclass serialises each call to a JSONL file so the
runner can reassemble a merged trace once the agent exits.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
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
        index: Sequence number within a single test run, assigned on append.
        timestamp: Monotonic-ish wall clock (seconds since epoch) used to sort
            calls when merging traces from multiple subprocesses.
    """

    tool: str
    arguments: dict[str, Any]
    result: Any | None = None
    error: str | None = None
    error_code: int | None = None
    latency_ms: float = 0.0
    server_name: str = ""
    index: int = 0
    timestamp: float = field(default_factory=time.time)

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
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecordedCall:
        return cls(
            tool=data["tool"],
            arguments=data.get("arguments") or {},
            result=data.get("result"),
            error=data.get("error"),
            error_code=data.get("error_code"),
            latency_ms=data.get("latency_ms", 0.0),
            server_name=data.get("server", ""),
            index=data.get("index", 0),
            timestamp=data.get("timestamp", 0.0),
        )


@dataclass
class CallLog:
    """In-memory append-only log of observed tool calls."""

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


class TraceFileCallLog(CallLog):
    """CallLog that also serialises each appended call to a JSONL file.

    Designed for subprocess mock servers: the runner creates the file,
    exports its path through `MCPTEST_TRACE_FILE`, and the mock server
    subprocess appends to it. Multiple mock servers (potentially different
    processes) can append to the same file concurrently because each
    append is a single `write()` that fits in the atomic-append regime of
    typical POSIX file systems for small payloads.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        # Ensure the file exists even before the first append so readers
        # don't race against creation.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, call: RecordedCall) -> RecordedCall:
        call = super().append(call)
        line = json.dumps(call.to_dict(), default=str) + "\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
        return call


TRACE_FILE_ENV = "MCPTEST_TRACE_FILE"


def default_call_log() -> CallLog:
    """Return a `TraceFileCallLog` if the env var is set, else a plain `CallLog`."""
    trace_path = os.environ.get(TRACE_FILE_ENV)
    if trace_path:
        return TraceFileCallLog(trace_path)
    return CallLog()


def read_trace_file(path: str | Path) -> list[RecordedCall]:
    """Read a JSONL trace file produced by `TraceFileCallLog`.

    Returns calls in file-append order, re-indexed sequentially so the
    downstream trace has a canonical 0..N-1 index regardless of which
    subprocess produced each call.
    """
    p = Path(path)
    if not p.exists():
        return []
    calls: list[RecordedCall] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        calls.append(RecordedCall.from_dict(data))
    calls.sort(key=lambda c: c.timestamp)
    for i, c in enumerate(calls):
        c.index = i
    return calls
