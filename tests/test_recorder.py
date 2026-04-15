"""Unit tests for the recorder module (RecordedCall, CallLog, TraceFileCallLog)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mcptest.mock_server.recorder import (
    TRACE_FILE_ENV,
    CallLog,
    RecordedCall,
    TraceFileCallLog,
    default_call_log,
    read_trace_file,
)


class TestRecordedCallSerialization:
    def test_to_dict_round_trip(self) -> None:
        c = RecordedCall(
            tool="t",
            arguments={"a": 1},
            result={"ok": True},
            latency_ms=10.5,
            server_name="s",
            index=3,
        )
        d = c.to_dict()
        c2 = RecordedCall.from_dict(d)
        assert c2.tool == "t"
        assert c2.arguments == {"a": 1}
        assert c2.result == {"ok": True}
        assert c2.latency_ms == 10.5
        assert c2.server_name == "s"
        assert c2.index == 3

    def test_timestamp_auto_populated(self) -> None:
        c = RecordedCall(tool="t", arguments={})
        assert c.timestamp > 0

    def test_from_dict_accepts_partial(self) -> None:
        c = RecordedCall.from_dict({"tool": "t"})
        assert c.tool == "t"
        assert c.arguments == {}
        assert c.index == 0


class TestTraceFileCallLog:
    def test_appends_jsonl(self, tmp_path: Path) -> None:
        p = tmp_path / "trace.jsonl"
        log = TraceFileCallLog(p)
        log.append(RecordedCall(tool="a", arguments={"x": 1}))
        log.append(RecordedCall(tool="b", arguments={"y": 2}))

        lines = p.read_text().strip().split("\n")
        assert len(lines) == 2
        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["tool"] == "a"
        assert parsed[1]["tool"] == "b"
        assert parsed[0]["index"] == 0
        assert parsed[1]["index"] == 1

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        p = tmp_path / "nested" / "deep" / "trace.jsonl"
        TraceFileCallLog(p)
        assert p.exists()

    def test_existing_file_not_clobbered(self, tmp_path: Path) -> None:
        p = tmp_path / "trace.jsonl"
        p.write_text('{"pre": "existing"}\n')
        log = TraceFileCallLog(p)
        log.append(RecordedCall(tool="a", arguments={}))

        lines = p.read_text().strip().split("\n")
        assert len(lines) == 2
        assert "pre" in lines[0]


class TestReadTraceFile:
    def test_reads_jsonl(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        log = TraceFileCallLog(p)
        log.append(RecordedCall(tool="a", arguments={}))
        log.append(RecordedCall(tool="b", arguments={}))

        calls = read_trace_file(p)
        assert [c.tool for c in calls] == ["a", "b"]
        assert [c.index for c in calls] == [0, 1]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert read_trace_file(tmp_path / "does-not-exist") == []

    def test_skips_blank_and_invalid_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        p.write_text(
            '{"tool": "a", "arguments": {}, "timestamp": 1.0}\n'
            "\n"
            "not-json\n"
            '{"tool": "b", "arguments": {}, "timestamp": 2.0}\n'
        )
        calls = read_trace_file(p)
        assert [c.tool for c in calls] == ["a", "b"]

    def test_sorts_by_timestamp(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        p.write_text(
            '{"tool": "second", "arguments": {}, "timestamp": 200}\n'
            '{"tool": "first", "arguments": {}, "timestamp": 100}\n'
            '{"tool": "third", "arguments": {}, "timestamp": 300}\n'
        )
        calls = read_trace_file(p)
        assert [c.tool for c in calls] == ["first", "second", "third"]
        assert [c.index for c in calls] == [0, 1, 2]


class TestDefaultCallLog:
    def test_env_unset_returns_plain_calllog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(TRACE_FILE_ENV, raising=False)
        log = default_call_log()
        assert isinstance(log, CallLog)
        assert not isinstance(log, TraceFileCallLog)

    def test_env_set_returns_file_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        p = tmp_path / "t.jsonl"
        monkeypatch.setenv(TRACE_FILE_ENV, str(p))
        log = default_call_log()
        assert isinstance(log, TraceFileCallLog)
        assert log.path == p
