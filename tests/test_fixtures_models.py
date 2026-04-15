"""Unit tests for fixture Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcptest.fixtures.models import (
    ErrorSpec,
    Fixture,
    Response,
    ResourceSpec,
    ServerSpec,
    ToolSpec,
)


class TestServerSpec:
    def test_minimal(self) -> None:
        s = ServerSpec(name="x")
        assert s.name == "x"
        assert s.version == "0.1.0"
        assert s.description is None

    def test_full(self) -> None:
        s = ServerSpec(name="x", version="2.0", description="desc")
        assert s.version == "2.0"
        assert s.description == "desc"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ServerSpec(name="")

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ServerSpec(name="x", unknown="y")  # type: ignore[call-arg]


class TestResponse:
    def test_return_value_via_alias(self) -> None:
        r = Response.model_validate({"return": {"ok": True}})
        assert r.return_value == {"ok": True}

    def test_return_text(self) -> None:
        r = Response(return_text="hello")
        assert r.return_text == "hello"

    def test_error_reference(self) -> None:
        r = Response(error="rate_limited")
        assert r.error == "rate_limited"

    def test_missing_body_rejected(self) -> None:
        with pytest.raises(ValidationError, match="one of"):
            Response()

    def test_multiple_bodies_rejected(self) -> None:
        with pytest.raises(ValidationError, match="only one"):
            Response.model_validate(
                {"return": {"a": 1}, "return_text": "x"}
            )

    def test_return_plus_error_rejected(self) -> None:
        with pytest.raises(ValidationError, match="only one"):
            Response.model_validate({"return": {"a": 1}, "error": "e"})

    def test_match_conditions(self) -> None:
        r = Response.model_validate(
            {"match": {"repo": "acme/api"}, "return": {"ok": True}}
        )
        assert r.match == {"repo": "acme/api"}

    def test_match_regex(self) -> None:
        r = Response.model_validate(
            {"match_regex": {"url": r"^https://"}, "return": {"ok": True}}
        )
        assert r.match_regex == {"url": r"^https://"}

    def test_default_flag(self) -> None:
        r = Response.model_validate({"default": True, "return": {"ok": True}})
        assert r.default is True

    def test_delay_ms(self) -> None:
        r = Response.model_validate({"delay_ms": 250, "return": {"ok": True}})
        assert r.delay_ms == 250

    def test_negative_delay_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Response.model_validate({"delay_ms": -5, "return": {"ok": True}})


class TestToolSpec:
    def test_minimal(self) -> None:
        t = ToolSpec(
            name="ping",
            responses=[Response(return_text="pong")],
        )
        assert t.name == "ping"
        assert t.description == ""
        assert t.input_schema == {"type": "object", "properties": {}}

    def test_empty_responses_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one response"):
            ToolSpec(name="x", responses=[])

    def test_multiple_defaults_rejected(self) -> None:
        with pytest.raises(ValidationError, match="default"):
            ToolSpec(
                name="x",
                responses=[
                    Response(default=True, return_text="a"),
                    Response(default=True, return_text="b"),
                ],
            )

    def test_one_default_ok(self) -> None:
        t = ToolSpec(
            name="x",
            responses=[
                Response(match={"q": "a"}, return_text="matched"),
                Response(default=True, return_text="fallback"),
            ],
        )
        assert len(t.responses) == 2


class TestResourceSpec:
    def test_minimal(self) -> None:
        r = ResourceSpec(uri="file:///x", content="hi")
        assert r.uri == "file:///x"
        assert r.mime_type == "text/plain"

    def test_full(self) -> None:
        r = ResourceSpec(
            uri="file:///x",
            name="x",
            description="d",
            mime_type="application/json",
            content="{}",
        )
        assert r.mime_type == "application/json"


class TestErrorSpec:
    def test_minimal(self) -> None:
        e = ErrorSpec(name="e", message="m")
        assert e.error_code == -32000
        assert e.tool is None

    def test_tool_scoped(self) -> None:
        e = ErrorSpec(name="e", tool="create_issue", message="m", error_code=-32001)
        assert e.tool == "create_issue"
        assert e.error_code == -32001

    def test_empty_message_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ErrorSpec(name="e", message="")


class TestFixture:
    def _tool(self, name: str) -> ToolSpec:
        return ToolSpec(name=name, responses=[Response(return_text="ok")])

    def test_minimal(self) -> None:
        f = Fixture(server=ServerSpec(name="x"))
        assert f.tools == []
        assert f.resources == []
        assert f.errors == []

    def test_find_tool(self) -> None:
        f = Fixture(server=ServerSpec(name="x"), tools=[self._tool("a"), self._tool("b")])
        assert f.find_tool("a").name == "a"  # type: ignore[union-attr]
        assert f.find_tool("b").name == "b"  # type: ignore[union-attr]
        assert f.find_tool("missing") is None

    def test_find_error(self) -> None:
        f = Fixture(
            server=ServerSpec(name="x"),
            errors=[ErrorSpec(name="e1", message="m"), ErrorSpec(name="e2", message="m")],
        )
        assert f.find_error("e1").name == "e1"  # type: ignore[union-attr]
        assert f.find_error("missing") is None

    def test_find_resource(self) -> None:
        f = Fixture(
            server=ServerSpec(name="x"),
            resources=[ResourceSpec(uri="file:///a", content="x")],
        )
        assert f.find_resource("file:///a").content == "x"  # type: ignore[union-attr]
        assert f.find_resource("file:///missing") is None

    def test_duplicate_tools_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate tool"):
            Fixture(
                server=ServerSpec(name="x"),
                tools=[self._tool("a"), self._tool("a")],
            )

    def test_duplicate_errors_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate error"):
            Fixture(
                server=ServerSpec(name="x"),
                errors=[
                    ErrorSpec(name="e", message="m"),
                    ErrorSpec(name="e", message="m"),
                ],
            )

    def test_duplicate_resources_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate resource"):
            Fixture(
                server=ServerSpec(name="x"),
                resources=[
                    ResourceSpec(uri="file:///a", content="x"),
                    ResourceSpec(uri="file:///a", content="y"),
                ],
            )

    def test_error_reference_resolves(self) -> None:
        f = Fixture(
            server=ServerSpec(name="x"),
            tools=[
                ToolSpec(
                    name="t",
                    responses=[Response(error="rate_limited")],
                )
            ],
            errors=[ErrorSpec(name="rate_limited", message="slow down")],
        )
        assert f.tools[0].responses[0].error == "rate_limited"

    def test_unresolved_error_reference_rejected(self) -> None:
        with pytest.raises(ValidationError, match="undefined error"):
            Fixture(
                server=ServerSpec(name="x"),
                tools=[
                    ToolSpec(
                        name="t",
                        responses=[Response(error="missing_error")],
                    )
                ],
            )
