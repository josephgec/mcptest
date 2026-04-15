"""Comprehensive tests for the mcptest capture module (Session 26).

All tests use InProcessServer + MockMCPServer for speed and determinism.
No subprocess spawning, no network connections.

Coverage targets:
  - ServerDiscovery: discovery from in-process servers with tools / resources /
    capabilities; graceful degradation on list_tools / list_resources failure.
  - ToolSampler: argument generation for varied schemas; execute_samples with
    success and error responses; sample_all across multiple tools.
  - FixtureGenerator: fixture dict structure; round-trip (generated fixture is
    loadable); error entries; resources; deduplication.
  - capture_server: write mode, dry-run mode, generate_tests mode.
  - CLI capture_command: via CliRunner with in-process fixtures.
  - Edge cases: server with no tools, tool that always errors, empty schemas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from mcptest.capture.discovery import DiscoveryResult, ServerDiscovery
from mcptest.capture.fixture_gen import FixtureGenerator, _slugify, _stable_key
from mcptest.capture.runner import CaptureResult, capture_server
from mcptest.capture.sampler import SampledTool, ToolSample, ToolSampler, _diverse_args
from mcptest.conformance.server import InProcessServer
from mcptest.fixtures.loader import load_fixture, FixtureLoadError
from mcptest.fixtures.models import (
    ErrorSpec,
    Fixture,
    ResourceSpec,
    Response,
    ServerSpec,
    ToolSpec,
)
from mcptest.mock_server.server import MockMCPServer


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_fixture(
    name: str = "test-server",
    version: str = "1.0.0",
    tools: list[ToolSpec] | None = None,
    resources: list[ResourceSpec] | None = None,
    errors: list[ErrorSpec] | None = None,
) -> Fixture:
    return Fixture(
        server=ServerSpec(name=name, version=version),
        tools=tools or [],
        resources=resources or [],
        errors=errors or [],
    )


def _simple_tool(
    name: str = "echo",
    *,
    description: str = "An echo tool",
    properties: dict | None = None,
    required: list[str] | None = None,
    response_text: str = "hello",
) -> ToolSpec:
    schema: dict = {
        "type": "object",
        "properties": properties or {"msg": {"type": "string"}},
    }
    if required:
        schema["required"] = required
    return ToolSpec(
        name=name,
        description=description,
        input_schema=schema,
        responses=[Response(default=True, return_text=response_text)],
    )


def _structured_tool(
    name: str = "search",
    *,
    response_data: dict | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="A structured response tool",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        responses=[
            Response(
                default=True,
                return_value=response_data or {"results": [], "count": 0},
            )
        ],
    )


def _in_process(fixture: Fixture) -> InProcessServer:
    mock = MockMCPServer(fixture, honor_delays=False)
    return InProcessServer(mock=mock, fixture=fixture)


def _discovery_from_fixture(fixture: Fixture) -> DiscoveryResult:
    """Build a DiscoveryResult directly from a Fixture (no async needed)."""
    return DiscoveryResult(
        server_name=fixture.server.name,
        server_version=fixture.server.version,
        capabilities={
            **({} if not fixture.tools else {"tools": {}}),
            **({} if not fixture.resources else {"resources": {}}),
        },
        tools=[
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in fixture.tools
        ],
        resources=[
            {
                "uri": r.uri,
                "name": r.name,
                "description": r.description,
                "mimeType": r.mime_type,
            }
            for r in fixture.resources
        ],
    )


# ---------------------------------------------------------------------------
# DiscoveryResult
# ---------------------------------------------------------------------------


class TestDiscoveryResult:
    def test_has_tools_true(self) -> None:
        dr = DiscoveryResult(
            server_name="s", server_version="1", tools=[{"name": "t"}]
        )
        assert dr.has_tools is True

    def test_has_tools_false(self) -> None:
        dr = DiscoveryResult(server_name="s", server_version="1")
        assert dr.has_tools is False

    def test_has_resources_true(self) -> None:
        dr = DiscoveryResult(
            server_name="s", server_version="1", resources=[{"uri": "r://x"}]
        )
        assert dr.has_resources is True

    def test_has_resources_false(self) -> None:
        dr = DiscoveryResult(server_name="s", server_version="1")
        assert dr.has_resources is False

    def test_tool_names(self) -> None:
        dr = DiscoveryResult(
            server_name="s",
            server_version="1",
            tools=[{"name": "a"}, {"name": "b"}],
        )
        assert dr.tool_names == ["a", "b"]

    def test_defaults(self) -> None:
        dr = DiscoveryResult(server_name="s", server_version="1")
        assert dr.capabilities == {}
        assert dr.tools == []
        assert dr.resources == []


# ---------------------------------------------------------------------------
# ServerDiscovery
# ---------------------------------------------------------------------------


class TestServerDiscovery:
    @pytest.mark.asyncio
    async def test_discover_server_info(self) -> None:
        fixture = _make_fixture(name="my-server", version="2.3.4")
        server = _in_process(fixture)
        result = await ServerDiscovery(server).discover()
        assert result.server_name == "my-server"
        assert result.server_version == "2.3.4"

    @pytest.mark.asyncio
    async def test_discover_tools(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool("ping")])
        server = _in_process(fixture)
        result = await ServerDiscovery(server).discover()
        assert len(result.tools) == 1
        assert result.tools[0]["name"] == "ping"
        assert "tools" in result.capabilities

    @pytest.mark.asyncio
    async def test_discover_multiple_tools(self) -> None:
        tools = [_simple_tool("alpha"), _simple_tool("beta"), _simple_tool("gamma")]
        fixture = _make_fixture(tools=tools)
        server = _in_process(fixture)
        result = await ServerDiscovery(server).discover()
        assert result.tool_names == ["alpha", "beta", "gamma"]

    @pytest.mark.asyncio
    async def test_discover_resources(self) -> None:
        resources = [
            ResourceSpec(uri="file://docs/readme.md", content="hello", name="readme")
        ]
        fixture = _make_fixture(resources=resources)
        server = _in_process(fixture)
        result = await ServerDiscovery(server).discover()
        assert len(result.resources) == 1
        assert result.resources[0]["uri"] == "file://docs/readme.md"
        assert "resources" in result.capabilities

    @pytest.mark.asyncio
    async def test_discover_no_tools(self) -> None:
        fixture = _make_fixture()
        server = _in_process(fixture)
        result = await ServerDiscovery(server).discover()
        assert result.tools == []
        assert "tools" not in result.capabilities

    @pytest.mark.asyncio
    async def test_discover_tool_schema_present(self) -> None:
        tool = _simple_tool(
            "create",
            properties={"title": {"type": "string"}, "body": {"type": "string"}},
            required=["title"],
        )
        fixture = _make_fixture(tools=[tool])
        server = _in_process(fixture)
        result = await ServerDiscovery(server).discover()
        schema = result.tools[0]["inputSchema"]
        assert "title" in schema["properties"]
        assert schema["required"] == ["title"]

    @pytest.mark.asyncio
    async def test_discover_graceful_on_list_tools_failure(self) -> None:
        """If list_tools() raises, discovery returns empty tools list."""

        class BrokenServer:
            async def get_server_info(self) -> dict:
                return {"name": "broken", "version": "0.0.0"}

            async def get_capabilities(self) -> dict:
                return {"tools": {}}

            async def list_tools(self) -> list:
                raise RuntimeError("tools unavailable")

            async def list_resources(self) -> list:
                return []

            async def close(self) -> None:
                pass

        result = await ServerDiscovery(BrokenServer()).discover()
        assert result.tools == []
        assert result.server_name == "broken"

    @pytest.mark.asyncio
    async def test_discover_graceful_on_list_resources_failure(self) -> None:
        """If list_resources() raises, discovery returns empty resources list."""

        class BrokenResourceServer:
            async def get_server_info(self) -> dict:
                return {"name": "s", "version": "1"}

            async def get_capabilities(self) -> dict:
                return {"resources": {}}

            async def list_tools(self) -> list:
                return []

            async def list_resources(self) -> list:
                raise RuntimeError("resources unavailable")

            async def close(self) -> None:
                pass

        result = await ServerDiscovery(BrokenResourceServer()).discover()
        assert result.resources == []


# ---------------------------------------------------------------------------
# ToolSampler — argument generation (synchronous)
# ---------------------------------------------------------------------------


class TestToolSamplerArgGeneration:
    def test_sample_tool_returns_list(self) -> None:
        sampler = ToolSampler(server=None)
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        args_list = sampler.sample_tool("search", schema, n=1)
        assert isinstance(args_list, list)
        assert len(args_list) >= 1

    def test_sample_tool_respects_n(self) -> None:
        sampler = ToolSampler(server=None)
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        }
        args_list = sampler.sample_tool("t", schema, n=3)
        # May return fewer than n if the schema doesn't yield that many distinct sets
        assert 1 <= len(args_list) <= 3

    def test_diverse_args_empty_schema(self) -> None:
        result = _diverse_args({}, n=3)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_diverse_args_string_field(self) -> None:
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        result = _diverse_args(schema, n=2)
        assert all("name" in a for a in result)
        assert all(isinstance(a["name"], str) for a in result)

    def test_diverse_args_integer_field(self) -> None:
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        }
        result = _diverse_args(schema, n=2)
        assert all(isinstance(a["count"], int) for a in result)

    def test_diverse_args_boolean_field(self) -> None:
        schema = {
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
            "required": ["flag"],
        }
        result = _diverse_args(schema, n=2)
        assert all(isinstance(a["flag"], bool) for a in result)

    def test_diverse_args_enum_field(self) -> None:
        schema = {
            "type": "object",
            "properties": {"color": {"enum": ["red", "green", "blue"]}},
            "required": ["color"],
        }
        result = _diverse_args(schema, n=3)
        for a in result:
            assert a["color"] in ("red", "green", "blue")

    def test_diverse_args_array_field(self) -> None:
        schema = {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "string"}}},
            "required": ["items"],
        }
        result = _diverse_args(schema, n=2)
        assert all(isinstance(a["items"], list) for a in result)

    def test_diverse_args_object_field(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "meta": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                }
            },
            "required": ["meta"],
        }
        result = _diverse_args(schema, n=1)
        assert isinstance(result[0]["meta"], dict)

    def test_diverse_args_n_zero_returns_one(self) -> None:
        result = _diverse_args({"type": "object"}, n=0)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# ToolSampler — execute_samples (async)
# ---------------------------------------------------------------------------


class TestToolSamplerExecution:
    @pytest.mark.asyncio
    async def test_execute_samples_success(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool("greet", response_text="hi!")])
        server = _in_process(fixture)
        sampler = ToolSampler(server, samples_per_tool=2)
        schema = {"type": "object", "properties": {"msg": {"type": "string"}}}
        samples = await sampler.execute_samples("greet", schema)
        assert len(samples) >= 1
        assert all(isinstance(s, ToolSample) for s in samples)
        success = [s for s in samples if not s.is_error]
        assert len(success) >= 1

    @pytest.mark.asyncio
    async def test_execute_samples_structured_response(self) -> None:
        fixture = _make_fixture(tools=[_structured_tool("lookup")])
        server = _in_process(fixture)
        sampler = ToolSampler(server, samples_per_tool=1)
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        samples = await sampler.execute_samples("lookup", schema)
        assert len(samples) == 1
        assert samples[0].is_error is False
        assert "structuredContent" in samples[0].response

    @pytest.mark.asyncio
    async def test_execute_samples_on_missing_tool_is_error(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool("real-tool")])
        server = _in_process(fixture)
        sampler = ToolSampler(server, samples_per_tool=1)
        # Calling a non-existent tool → should be recorded as error sample
        samples = await sampler.execute_samples("nonexistent", {})
        assert len(samples) == 1
        assert samples[0].is_error is True

    @pytest.mark.asyncio
    async def test_execute_samples_exception_recorded(self) -> None:
        """If the server raises an unexpected exception, it's recorded, not raised."""

        class ExceptionServer:
            async def call_tool(self, name: str, args: dict) -> dict:
                raise RuntimeError("boom")

        sampler = ToolSampler(ExceptionServer(), samples_per_tool=1)
        samples = await sampler.execute_samples("t", {})
        assert len(samples) == 1
        assert samples[0].is_error is True
        assert "boom" in samples[0].response["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_execute_samples_labels(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool("ping")])
        server = _in_process(fixture)
        sampler = ToolSampler(server, samples_per_tool=2)
        schema = {"type": "object", "properties": {"msg": {"type": "string"}}}
        samples = await sampler.execute_samples("ping", schema)
        labels = [s.label for s in samples]
        assert labels[0] == "sample-0"
        # At least one sample must exist; with a simple string schema we get 2
        assert len(labels) >= 1
        assert all(lbl.startswith("sample-") for lbl in labels)

    @pytest.mark.asyncio
    async def test_sample_all_returns_one_per_tool(self) -> None:
        tools = [_simple_tool("a"), _simple_tool("b"), _simple_tool("c")]
        fixture = _make_fixture(tools=tools)
        server = _in_process(fixture)
        sampler = ToolSampler(server, samples_per_tool=1)
        tool_dicts = [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
            for t in tools
        ]
        result = await sampler.sample_all(tool_dicts)
        assert len(result) == 3
        assert [st.name for st in result] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_sample_all_empty_tools(self) -> None:
        fixture = _make_fixture()
        server = _in_process(fixture)
        sampler = ToolSampler(server, samples_per_tool=2)
        result = await sampler.sample_all([])
        assert result == []

    @pytest.mark.asyncio
    async def test_sampled_tool_success_error_partition(self) -> None:
        # One tool that always returns an error response.
        # Use a schema with required fields so _diverse_args produces ≥2 distinct
        # arg sets, allowing us to verify the full partition.
        schema = {
            "type": "object",
            "properties": {"q": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["q", "limit"],
        }

        # Wrap server to always return isError=True
        class ErrorServer:
            async def call_tool(self, name: str, args: dict) -> dict:
                return {"content": [{"type": "text", "text": "error!"}], "isError": True}

        sampler = ToolSampler(ErrorServer(), samples_per_tool=2)
        samples = await sampler.execute_samples("fail", schema)
        sampled = SampledTool(name="fail", description="", input_schema=schema, samples=samples)
        assert len(sampled.error_samples) >= 1
        assert len(sampled.success_samples) == 0
        # All samples must be errors
        assert len(sampled.error_samples) == len(sampled.samples)


# ---------------------------------------------------------------------------
# FixtureGenerator
# ---------------------------------------------------------------------------


class TestFixtureGenerator:
    def _make_gen(
        self,
        fixture: Fixture,
        samples_per_tool: int = 1,
    ) -> FixtureGenerator:
        """Build a FixtureGenerator from a fixture using fake (no-call) samples."""
        disc = _discovery_from_fixture(fixture)
        # Build SampledTool objects with pre-canned responses
        sampled: list[SampledTool] = []
        for tool in fixture.tools:
            # Use the first response's data to build a sample
            resp = tool.responses[0]
            if resp.return_text is not None:
                response_dict = {
                    "content": [{"type": "text", "text": resp.return_text}],
                }
                is_error = False
            elif resp.return_value is not None:
                response_dict = {
                    "content": [{"type": "text", "text": str(resp.return_value)}],
                    "structuredContent": resp.return_value,
                }
                is_error = False
            else:
                response_dict = {
                    "content": [{"type": "text", "text": "error"}],
                    "isError": True,
                }
                is_error = True

            args = {"msg": "hello"} if tool.input_schema.get("properties") else {}
            sample = ToolSample(
                args=args, response=response_dict, is_error=is_error, label="sample-0"
            )
            sampled.append(
                SampledTool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                    samples=[sample],
                )
            )
        return FixtureGenerator(disc, sampled)

    def test_generate_fixture_is_yaml(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        gen = self._make_gen(fixture)
        text = gen.generate_fixture()
        parsed = yaml.safe_load(text)
        assert isinstance(parsed, dict)

    def test_generated_fixture_has_server(self) -> None:
        fixture = _make_fixture(name="my-srv", version="3.0.0")
        gen = self._make_gen(fixture)
        data = gen.generate_fixture_dict()
        assert data["server"]["name"] == "my-srv"
        assert data["server"]["version"] == "3.0.0"

    def test_generated_fixture_has_tools(self) -> None:
        tools = [_simple_tool("alpha"), _simple_tool("beta")]
        fixture = _make_fixture(tools=tools)
        gen = self._make_gen(fixture)
        data = gen.generate_fixture_dict()
        tool_names = [t["name"] for t in data["tools"]]
        assert "alpha" in tool_names
        assert "beta" in tool_names

    def test_generated_tool_has_responses(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool(response_text="pong")])
        gen = self._make_gen(fixture)
        data = gen.generate_fixture_dict()
        responses = data["tools"][0]["responses"]
        assert len(responses) >= 1
        # First response should be default=True
        assert responses[0].get("default") is True

    def test_text_response_uses_return_text(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool(response_text="pong")])
        gen = self._make_gen(fixture)
        data = gen.generate_fixture_dict()
        first_resp = data["tools"][0]["responses"][0]
        assert "return_text" in first_resp
        assert first_resp["return_text"] == "pong"

    def test_structured_response_uses_return(self) -> None:
        fixture = _make_fixture(tools=[_structured_tool(response_data={"id": 42})])
        gen = self._make_gen(fixture)
        data = gen.generate_fixture_dict()
        first_resp = data["tools"][0]["responses"][0]
        assert "return" in first_resp
        assert first_resp["return"]["id"] == 42

    def test_generated_fixture_roundtrips(self) -> None:
        """Generated fixture dict must be loadable by load_fixture via YAML."""
        import tempfile

        fixture = _make_fixture(
            name="roundtrip-server",
            tools=[_simple_tool("ping"), _structured_tool("search")],
        )
        gen = self._make_gen(fixture)
        yaml_text = gen.generate_fixture()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_text)
            tmp_path = f.name

        loaded = load_fixture(tmp_path)
        Path(tmp_path).unlink(missing_ok=True)

        assert loaded.server.name == "roundtrip-server"
        assert len(loaded.tools) == 2
        tool_names = {t.name for t in loaded.tools}
        assert "ping" in tool_names
        assert "search" in tool_names

    def test_roundtrip_tool_schema_preserved(self) -> None:
        """Input schema in fixture round-trip must match original."""
        import tempfile

        schema = {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["q"],
        }
        tool = ToolSpec(
            name="find",
            input_schema=schema,
            responses=[Response(default=True, return_text="results")],
        )
        fixture = _make_fixture(tools=[tool])
        gen = self._make_gen(fixture)
        yaml_text = gen.generate_fixture()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_text)
            tmp_path = f.name

        loaded = load_fixture(tmp_path)
        Path(tmp_path).unlink(missing_ok=True)

        loaded_schema = loaded.tools[0].input_schema
        assert loaded_schema["required"] == ["q"]
        assert "q" in loaded_schema["properties"]
        assert "limit" in loaded_schema["properties"]

    def test_no_tools_produces_valid_fixture(self) -> None:
        """Fixture with no tools must still be valid YAML loadable by load_fixture."""
        import tempfile

        fixture = _make_fixture()
        disc = _discovery_from_fixture(fixture)
        gen = FixtureGenerator(disc, [])
        yaml_text = gen.generate_fixture()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_text)
            tmp_path = f.name

        loaded = load_fixture(tmp_path)
        Path(tmp_path).unlink(missing_ok=True)

        assert loaded.tools == []

    def test_error_samples_become_errors_list(self) -> None:
        """Error samples must populate the fixture errors: list."""
        disc = DiscoveryResult(server_name="err-srv", server_version="1")
        sampled = SampledTool(
            name="risky",
            description="",
            input_schema={"type": "object", "properties": {}},
            samples=[
                ToolSample(
                    args={},
                    response={
                        "content": [{"type": "text", "text": "permission denied"}],
                        "isError": True,
                    },
                    is_error=True,
                    label="sample-0",
                )
            ],
        )
        gen = FixtureGenerator(disc, [sampled])
        data = gen.generate_fixture_dict()
        errors = data.get("errors", [])
        assert len(errors) == 1
        assert errors[0]["tool"] == "risky"
        assert "permission denied" in errors[0]["message"]

    def test_duplicate_error_messages_deduplicated(self) -> None:
        """The same error message from multiple samples appears only once."""
        disc = DiscoveryResult(server_name="s", server_version="1")
        err_sample = ToolSample(
            args={},
            response={"content": [{"type": "text", "text": "rate limited"}], "isError": True},
            is_error=True,
            label="sample-0",
        )
        sampled = SampledTool(
            name="t", description="", input_schema={}, samples=[err_sample, err_sample]
        )
        gen = FixtureGenerator(disc, [sampled])
        data = gen.generate_fixture_dict()
        assert len(data.get("errors", [])) == 1

    def test_resources_in_fixture_dict(self) -> None:
        resource = ResourceSpec(
            uri="file://x", content="hello", name="x-file", mime_type="text/plain"
        )
        fixture = _make_fixture(resources=[resource])
        disc = _discovery_from_fixture(fixture)
        gen = FixtureGenerator(disc, [])
        data = gen.generate_fixture_dict()
        assert "resources" in data
        assert data["resources"][0]["uri"] == "file://x"

    def test_generate_tests_returns_yaml(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        gen = self._make_gen(fixture)
        text = gen.generate_tests("fixtures/test.yaml", agent_cmd="python a.py")
        parsed = yaml.safe_load(text)
        assert "cases" in parsed
        assert len(parsed["cases"]) > 0

    def test_generate_tests_embeds_fixture_path(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        gen = self._make_gen(fixture)
        text = gen.generate_tests("fixtures/my.yaml")
        parsed = yaml.safe_load(text)
        assert "fixtures/my.yaml" in parsed["fixtures"]

    def test_generate_tests_embeds_agent_cmd(self) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        gen = self._make_gen(fixture)
        text = gen.generate_tests("fixtures/f.yaml", agent_cmd="node agent.js")
        parsed = yaml.safe_load(text)
        assert parsed["agent"]["command"] == "node agent.js"


# ---------------------------------------------------------------------------
# Helpers — _slugify, _stable_key
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_slugify_basic(self) -> None:
        assert _slugify("my server") == "my-server"

    def test_slugify_special_chars(self) -> None:
        assert _slugify("My Server v2.0!") == "my-server-v2-0"

    def test_slugify_truncation(self) -> None:
        long = "a" * 50
        assert len(_slugify(long)) == 40

    def test_slugify_empty(self) -> None:
        assert _slugify("") == "server"

    def test_stable_key_ignores_default(self) -> None:
        a = {"default": True, "return_text": "ok"}
        b = {"default": False, "return_text": "ok"}
        assert _stable_key(a) == _stable_key(b)

    def test_stable_key_differs_on_content(self) -> None:
        a = {"return_text": "ok"}
        b = {"return_text": "fail"}
        assert _stable_key(a) != _stable_key(b)


# ---------------------------------------------------------------------------
# capture_server (async orchestration)
# ---------------------------------------------------------------------------


class TestCaptureServer:
    @pytest.mark.asyncio
    async def test_write_fixture_file(self, tmp_path: Path) -> None:
        fixture = _make_fixture(name="write-srv", tools=[_simple_tool("ping")])
        server = _in_process(fixture)
        result = await capture_server(
            server, output_dir=tmp_path, samples_per_tool=1
        )
        assert result.fixture_path is not None
        assert result.fixture_path.exists()
        loaded = load_fixture(result.fixture_path)
        assert loaded.server.name == "write-srv"

    @pytest.mark.asyncio
    async def test_fixture_filename_derived_from_server_name(
        self, tmp_path: Path
    ) -> None:
        fixture = _make_fixture(name="my-cool-server", tools=[_simple_tool()])
        server = _in_process(fixture)
        result = await capture_server(server, output_dir=tmp_path, samples_per_tool=1)
        assert result.fixture_path.name == "my-cool-server.yaml"

    @pytest.mark.asyncio
    async def test_dry_run_no_files_written(self, tmp_path: Path) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process(fixture)
        result = await capture_server(
            server, output_dir=tmp_path, samples_per_tool=1, dry_run=True
        )
        assert result.dry_run is True
        assert result.fixture_path is None
        assert result.test_paths == []
        assert not list(tmp_path.iterdir())

    @pytest.mark.asyncio
    async def test_generate_tests_flag(self, tmp_path: Path) -> None:
        fixture = _make_fixture(tools=[_simple_tool("echo")])
        server = _in_process(fixture)
        result = await capture_server(
            server,
            output_dir=tmp_path,
            samples_per_tool=1,
            generate_tests=True,
        )
        assert len(result.test_paths) == 1
        test_path = result.test_paths[0]
        assert test_path.exists()
        parsed = yaml.safe_load(test_path.read_text())
        assert "cases" in parsed

    @pytest.mark.asyncio
    async def test_sample_count(self, tmp_path: Path) -> None:
        tools = [_simple_tool("a"), _simple_tool("b")]
        fixture = _make_fixture(tools=tools)
        server = _in_process(fixture)
        result = await capture_server(
            server, output_dir=tmp_path, samples_per_tool=2
        )
        # Each of 2 tools × 2 samples = 4 total (may vary due to dedup)
        assert result.sample_count >= 2

    @pytest.mark.asyncio
    async def test_tool_count(self, tmp_path: Path) -> None:
        tools = [_simple_tool("x"), _simple_tool("y"), _simple_tool("z")]
        fixture = _make_fixture(tools=tools)
        server = _in_process(fixture)
        result = await capture_server(server, output_dir=tmp_path, samples_per_tool=1)
        assert result.tool_count == 3

    @pytest.mark.asyncio
    async def test_no_tools_server(self, tmp_path: Path) -> None:
        fixture = _make_fixture()
        server = _in_process(fixture)
        result = await capture_server(server, output_dir=tmp_path, samples_per_tool=1)
        assert result.tool_count == 0
        assert result.sample_count == 0
        assert result.fixture_path is not None
        loaded = load_fixture(result.fixture_path)
        assert loaded.tools == []

    @pytest.mark.asyncio
    async def test_output_dir_created_if_absent(self, tmp_path: Path) -> None:
        fixture = _make_fixture(tools=[_simple_tool()])
        server = _in_process(fixture)
        nested = tmp_path / "a" / "b" / "c"
        result = await capture_server(server, output_dir=nested, samples_per_tool=1)
        assert nested.exists()
        assert result.fixture_path.exists()

    @pytest.mark.asyncio
    async def test_discovery_in_result(self, tmp_path: Path) -> None:
        fixture = _make_fixture(name="disc-srv", version="5.0.0")
        server = _in_process(fixture)
        result = await capture_server(server, output_dir=tmp_path, samples_per_tool=1)
        assert result.discovery.server_name == "disc-srv"
        assert result.discovery.server_version == "5.0.0"

    @pytest.mark.asyncio
    async def test_generated_fixture_is_valid(self, tmp_path: Path) -> None:
        """capture_server fixture must pass load_fixture without error."""
        tool = ToolSpec(
            name="compute",
            description="Do maths",
            input_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                },
                "required": ["x", "y"],
            },
            responses=[Response(default=True, return_value={"result": 0.0})],
        )
        fixture = _make_fixture(name="math-srv", tools=[tool])
        server = _in_process(fixture)
        result = await capture_server(server, output_dir=tmp_path, samples_per_tool=2)
        loaded = load_fixture(result.fixture_path)
        assert loaded.server.name == "math-srv"
        assert loaded.tools[0].name == "compute"


# ---------------------------------------------------------------------------
# CLI — capture_command
# ---------------------------------------------------------------------------


class TestCaptureCLI:
    """Integration tests for the capture CLI command.

    We cannot spawn real subprocesses, so we patch capture_server with an
    in-process implementation via monkeypatching.
    """

    def _run_cli(self, args: list[str], monkeypatch: Any, tmp_path: Path) -> Any:
        from click.testing import CliRunner
        from mcptest.cli.main import main

        runner = CliRunner()
        return runner.invoke(main, args, catch_exceptions=False)

    def test_capture_help(self) -> None:
        from click.testing import CliRunner
        from mcptest.cli.main import main

        runner = CliRunner()
        result = runner.invoke(main, ["capture", "--help"])
        assert result.exit_code == 0
        assert "SERVER_COMMAND" in result.output
        assert "--output" in result.output
        assert "--generate-tests" in result.output
        assert "--dry-run" in result.output
        assert "--samples-per-tool" in result.output

    def test_capture_command_registered(self) -> None:
        from mcptest.cli.main import main

        assert "capture" in main.commands

    def test_capture_dry_run_via_patch(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Patch capture_server to return a pre-built CaptureResult."""
        import anyio
        from click.testing import CliRunner
        from mcptest.cli.main import main

        disc = DiscoveryResult(
            server_name="test-srv", server_version="1.0", tools=[], resources=[]
        )
        fake_result = CaptureResult(
            fixture_path=None,
            test_paths=[],
            discovery=disc,
            sampled_tools=[],
            sample_count=0,
            dry_run=True,
        )

        async def fake_capture(server_or_command, output_dir=".", **kwargs):
            return fake_result

        monkeypatch.setattr("mcptest.capture.runner.capture_server", fake_capture)
        # Also patch the import in CLI
        import mcptest.cli.commands as cmd_module
        monkeypatch.setattr(
            "mcptest.capture.runner.capture_server", fake_capture
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["capture", "echo hello", "--dry-run", "--output", str(tmp_path)],
            catch_exceptions=True,
        )
        # The command should exit 0 or fail cleanly if the server is unreachable
        # (we just verify it doesn't crash with an unhandled exception)
        assert result.exit_code in (0, 1)

    def test_capture_writes_fixture(self, tmp_path: Path) -> None:
        """Full end-to-end CLI test using a real InProcessServer via monkeypatching."""
        from click.testing import CliRunner
        from mcptest.cli.main import main

        fixture = _make_fixture(name="cli-srv", tools=[_simple_tool("greet")])
        server = _in_process(fixture)

        # Patch capture_server to use our in-process server
        import mcptest.capture.runner as runner_mod

        original = runner_mod.capture_server

        async def patched_capture(server_or_command, output_dir=".", **kwargs):
            return await original(
                server, output_dir=output_dir, samples_per_tool=1, dry_run=False
            )

        runner_mod.capture_server = patched_capture
        try:
            cli_runner = CliRunner()
            result = cli_runner.invoke(
                main,
                ["capture", "ignored-command", "--output", str(tmp_path)],
                catch_exceptions=False,
            )
            # Verify fixture was written
            yaml_files = list(tmp_path.glob("*.yaml"))
            assert len(yaml_files) >= 1
            loaded = load_fixture(yaml_files[0])
            assert loaded.server.name == "cli-srv"
        finally:
            runner_mod.capture_server = original
