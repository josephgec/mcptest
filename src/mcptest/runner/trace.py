"""Trace dataclass — the canonical record of one agent run.

A `Trace` captures everything assertions need to evaluate: every mocked tool
call with its arguments, result, error state, latency; the agent's final
textual output; its exit code; and total duration. Traces serialize to JSON
so they can be snapshotted (Session 9) or shipped to the cloud backend
(Session 12).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcptest.mock_server.recorder import RecordedCall


def _new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Trace:
    """One completed agent run."""

    trace_id: str = field(default_factory=_new_trace_id)
    timestamp: str = field(default_factory=_utc_iso_now)
    input: str = ""
    output: str = ""
    tool_calls: list[RecordedCall] = field(default_factory=list)
    duration_s: float = 0.0
    exit_code: int = 0
    stderr: str = ""
    agent_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tool_calls(self) -> int:
        return len(self.tool_calls)

    @property
    def tool_names(self) -> list[str]:
        """Ordered list of tool names that were called (with duplicates)."""
        return [c.tool for c in self.tool_calls]

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.agent_error is None

    def calls_to(self, tool_name: str) -> list[RecordedCall]:
        return [c for c in self.tool_calls if c.tool == tool_name]

    def call_count(self, tool_name: str) -> int:
        return len(self.calls_to(tool_name))

    def errors(self) -> list[RecordedCall]:
        return [c for c in self.tool_calls if c.is_error]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "input": self.input,
            "output": self.output,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "duration_s": self.duration_s,
            "exit_code": self.exit_code,
            "stderr": self.stderr,
            "agent_error": self.agent_error,
            "metadata": self.metadata,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trace:
        return cls(
            trace_id=data.get("trace_id", _new_trace_id()),
            timestamp=data.get("timestamp", _utc_iso_now()),
            input=data.get("input", ""),
            output=data.get("output", ""),
            tool_calls=[RecordedCall.from_dict(c) for c in data.get("tool_calls", [])],
            duration_s=data.get("duration_s", 0.0),
            exit_code=data.get("exit_code", 0),
            stderr=data.get("stderr", ""),
            agent_error=data.get("agent_error"),
            metadata=data.get("metadata", {}),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Trace:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
