"""Unit tests for the Trace dataclass."""

from __future__ import annotations

import json
from pathlib import Path

from mcptest.mock_server.recorder import RecordedCall
from mcptest.runner.trace import Trace


def _call(
    tool: str, *, error: str | None = None, arguments: dict[str, object] | None = None
) -> RecordedCall:
    return RecordedCall(
        tool=tool,
        arguments=arguments or {},
        error=error,
    )


class TestTraceProperties:
    def test_empty_trace(self) -> None:
        t = Trace(input="hi")
        assert t.total_tool_calls == 0
        assert t.tool_names == []
        assert t.succeeded is True
        assert t.calls_to("x") == []
        assert t.call_count("x") == 0
        assert t.errors() == []

    def test_tool_names_ordered(self) -> None:
        t = Trace(tool_calls=[_call("a"), _call("b"), _call("a")])
        assert t.tool_names == ["a", "b", "a"]
        assert t.call_count("a") == 2
        assert t.call_count("b") == 1
        assert len(t.calls_to("a")) == 2

    def test_errors(self) -> None:
        t = Trace(
            tool_calls=[
                _call("a"),
                _call("b", error="boom"),
                _call("c"),
            ]
        )
        errs = t.errors()
        assert len(errs) == 1
        assert errs[0].tool == "b"

    def test_succeeded_false_on_exit_code(self) -> None:
        t = Trace(exit_code=1)
        assert t.succeeded is False

    def test_succeeded_false_on_agent_error(self) -> None:
        t = Trace(agent_error="timeout")
        assert t.succeeded is False


class TestTraceSerialization:
    def test_to_dict(self) -> None:
        t = Trace(input="x", output="y", tool_calls=[_call("a")], exit_code=0)
        d = t.to_dict()
        assert d["input"] == "x"
        assert d["output"] == "y"
        assert len(d["tool_calls"]) == 1
        assert d["tool_calls"][0]["tool"] == "a"

    def test_to_json_parseable(self) -> None:
        t = Trace(input="x")
        parsed = json.loads(t.to_json())
        assert parsed["input"] == "x"

    def test_roundtrip_dict(self) -> None:
        t = Trace(
            input="i",
            output="o",
            tool_calls=[_call("a", arguments={"k": 1})],
            duration_s=1.5,
            exit_code=2,
            stderr="oops",
        )
        t2 = Trace.from_dict(t.to_dict())
        assert t2.input == "i"
        assert t2.output == "o"
        assert t2.duration_s == 1.5
        assert t2.exit_code == 2
        assert t2.stderr == "oops"
        assert t2.tool_calls[0].tool == "a"
        assert t2.tool_calls[0].arguments == {"k": 1}

    def test_from_dict_defaults(self) -> None:
        t = Trace.from_dict({})
        assert t.input == ""
        assert t.tool_calls == []
        assert t.exit_code == 0

    def test_save_and_load(self, tmp_path: Path) -> None:
        t = Trace(input="x", tool_calls=[_call("a")])
        p = tmp_path / "trace.json"
        t.save(p)
        t2 = Trace.load(p)
        assert t2.input == "x"
        assert t2.tool_calls[0].tool == "a"

    def test_auto_trace_id_and_timestamp(self) -> None:
        t = Trace()
        assert len(t.trace_id) == 12
        assert "T" in t.timestamp  # ISO format
