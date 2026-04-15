"""Tests for the SSE/HTTP transport of the mock server.

The MCP SDK's SSE transport is itself covered by that project's test suite —
we verify only that `MockMCPServer.build_sse_app()` assembles a Starlette app
with the expected routes and that the CLI correctly dispatches to stdio vs
SSE transports. Running a full real-socket SSE round-trip is deferred to
the MCP SDK's own integration suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mcptest.fixtures.models import (
    Fixture,
    Response,
    ServerSpec,
    ToolSpec,
)
from mcptest.mock_server import MockMCPServer


def _fixture() -> Fixture:
    return Fixture(
        server=ServerSpec(name="sse-mock", version="1.0"),
        tools=[
            ToolSpec(
                name="ping",
                responses=[Response(return_text="pong")],
            )
        ],
    )


class TestBuildSseApp:
    def test_returns_starlette_app(self) -> None:
        from starlette.applications import Starlette

        app = MockMCPServer(_fixture()).build_sse_app()
        assert isinstance(app, Starlette)

    def test_routes_present(self) -> None:
        app = MockMCPServer(_fixture()).build_sse_app()
        route_paths = []
        for r in app.routes:
            if hasattr(r, "path"):
                route_paths.append(r.path)
        assert "/sse" in route_paths
        assert any(p.startswith("/messages") for p in route_paths)

    def test_custom_endpoint(self) -> None:
        app = MockMCPServer(_fixture()).build_sse_app(endpoint="/bus/")
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert any("bus" in p for p in paths)


class TestMainModuleTransport:
    def test_default_stdio(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mcptest.mock_server import __main__ as main_mod

        p = tmp_path / "f.yaml"
        p.write_text(
            "server: { name: x }\n"
            "tools:\n"
            "  - name: ping\n"
            "    responses:\n"
            "      - return_text: pong\n"
        )

        captured: dict[str, Any] = {}

        def fake_run(func: Any) -> None:
            captured["func"] = func

        monkeypatch.setattr(main_mod.anyio, "run", fake_run)
        rc = main_mod.main([str(p)])
        assert rc == 0
        assert captured["func"].__name__ == "run_stdio"

    def test_sse_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mcptest.mock_server import __main__ as main_mod

        p = tmp_path / "f.yaml"
        p.write_text(
            "server: { name: x }\n"
            "tools:\n"
            "  - name: ping\n"
            "    responses:\n"
            "      - return_text: pong\n"
        )

        captured: dict[str, Any] = {}

        def fake_run(func: Any) -> None:
            captured["func"] = func

        monkeypatch.setattr(main_mod.anyio, "run", fake_run)
        rc = main_mod.main(
            [str(p), "--sse", "--host", "127.0.0.1", "--port", "9999"]
        )
        assert rc == 0
        # The SSE branch wraps run_sse in a local async helper named "_run_sse".
        assert captured["func"].__name__ == "_run_sse"

    def test_transport_sse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mcptest.mock_server import __main__ as main_mod

        p = tmp_path / "f.yaml"
        p.write_text(
            "server: { name: x }\n"
            "tools:\n"
            "  - name: ping\n"
            "    responses:\n"
            "      - return_text: pong\n"
        )

        captured: dict[str, Any] = {}

        def fake_run(func: Any) -> None:
            captured["func"] = func

        monkeypatch.setattr(main_mod.anyio, "run", fake_run)
        rc = main_mod.main([str(p), "--transport", "sse"])
        assert rc == 0
        assert captured["func"].__name__ == "_run_sse"

    def test_sse_nested_helper_delegates_to_run_sse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Drive the nested `_run_sse` helper actually-end-to-end (with
        `run_sse` stubbed) so that line gets exercised."""
        from mcptest.mock_server import __main__ as main_mod
        from mcptest.mock_server.server import MockMCPServer

        p = tmp_path / "f.yaml"
        p.write_text(
            "server: { name: x }\n"
            "tools:\n"
            "  - name: ping\n"
            "    responses:\n"
            "      - return_text: pong\n"
        )

        captured: dict[str, Any] = {}

        async def fake_run_sse(
            self: MockMCPServer,
            host: str = "127.0.0.1",
            port: int = 8765,
            endpoint: str = "/messages/",
        ) -> None:
            captured["host"] = host
            captured["port"] = port
            captured["endpoint"] = endpoint

        monkeypatch.setattr(MockMCPServer, "run_sse", fake_run_sse)
        rc = main_mod.main(
            [str(p), "--sse", "--host", "10.0.0.1", "--port", "4242"]
        )
        assert rc == 0
        assert captured["host"] == "10.0.0.1"
        assert captured["port"] == 4242
        assert captured["endpoint"] == "/messages/"

    def test_bad_fixture(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from mcptest.mock_server import __main__ as main_mod

        bad = tmp_path / "bad.yaml"
        bad.write_text("[unclosed\n")
        rc = main_mod.main([str(bad)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "could not load" in err

    def test_missing_arg(self, capsys: pytest.CaptureFixture[str]) -> None:
        from mcptest.mock_server import __main__ as main_mod

        rc = main_mod.main([])
        assert rc != 0
