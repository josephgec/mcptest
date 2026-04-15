"""Pydantic models describing mcptest YAML fixtures.

A *fixture* declares a fake MCP server: its metadata, its tools, their declared
input schemas, canned responses (optionally conditional on input parameters),
simulated resources, and named error scenarios that tests can inject.

The models are the single source of truth for the fixture schema — the YAML
loader validates against them, and the mock server consumes them at runtime.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ServerSpec(BaseModel):
    """Metadata about the fake MCP server."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    version: str = "0.1.0"
    description: str | None = None


class Response(BaseModel):
    """One possible response entry for a mocked tool call.

    Matching proceeds top-to-bottom over a tool's `responses` list. The first
    entry whose `match`/`match_regex` conditions are satisfied by the incoming
    arguments is used. An entry with `default: true` acts as a fallback.

    A response must specify exactly one of:
    - `return` — a JSON-serializable dict returned as the tool result payload
    - `return_text` — a plain-text result
    - `error` — the *name* of an error scenario declared in the fixture's
      top-level `errors:` list, which is raised instead of returning content
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    match: dict[str, Any] | None = None
    match_regex: dict[str, str] | None = None
    default: bool = False
    return_value: dict[str, Any] | None = Field(default=None, alias="return")
    return_text: str | None = None
    delay_ms: int = Field(default=0, ge=0)
    error: str | None = None

    @model_validator(mode="after")
    def _exactly_one_body(self) -> Response:
        bodies = [self.return_value, self.return_text, self.error]
        present = sum(b is not None for b in bodies)
        if present == 0:
            raise ValueError(
                "response must specify one of: return, return_text, error"
            )
        if present > 1:
            raise ValueError(
                "response must specify only one of: return, return_text, error"
            )
        return self


class ToolSpec(BaseModel):
    """A mocked tool that the fake MCP server exposes."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    responses: list[Response] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_one_response(self) -> ToolSpec:
        if not self.responses:
            raise ValueError(f"tool {self.name!r} must declare at least one response")
        return self

    @model_validator(mode="after")
    def _at_most_one_default(self) -> ToolSpec:
        defaults = sum(1 for r in self.responses if r.default)
        if defaults > 1:
            raise ValueError(
                f"tool {self.name!r} declares {defaults} default responses; at most one allowed"
            )
        return self


class ResourceSpec(BaseModel):
    """A mocked MCP resource (file-like content addressable by URI)."""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(..., min_length=1)
    name: str | None = None
    description: str | None = None
    mime_type: str = "text/plain"
    content: str


class ErrorSpec(BaseModel):
    """A named error scenario that a test can inject or a response can trigger."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    tool: str | None = None
    error_code: int = -32000
    message: str = Field(..., min_length=1)


class Fixture(BaseModel):
    """The parsed form of one YAML fixture file."""

    model_config = ConfigDict(extra="forbid")

    server: ServerSpec
    tools: list[ToolSpec] = Field(default_factory=list)
    resources: list[ResourceSpec] = Field(default_factory=list)
    errors: list[ErrorSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> Fixture:
        tool_names = [t.name for t in self.tools]
        if len(tool_names) != len(set(tool_names)):
            dupes = sorted({n for n in tool_names if tool_names.count(n) > 1})
            raise ValueError(f"duplicate tool names in fixture: {dupes}")

        error_names = [e.name for e in self.errors]
        if len(error_names) != len(set(error_names)):
            dupes = sorted({n for n in error_names if error_names.count(n) > 1})
            raise ValueError(f"duplicate error names in fixture: {dupes}")

        resource_uris = [r.uri for r in self.resources]
        if len(resource_uris) != len(set(resource_uris)):
            dupes = sorted({u for u in resource_uris if resource_uris.count(u) > 1})
            raise ValueError(f"duplicate resource URIs in fixture: {dupes}")

        return self

    @model_validator(mode="after")
    def _error_references_resolve(self) -> Fixture:
        error_names = {e.name for e in self.errors}
        for tool in self.tools:
            for idx, response in enumerate(tool.responses):
                if response.error and response.error not in error_names:
                    raise ValueError(
                        f"tool {tool.name!r} response #{idx} references "
                        f"undefined error {response.error!r}"
                    )
        return self

    def find_tool(self, name: str) -> ToolSpec | None:
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def find_error(self, name: str) -> ErrorSpec | None:
        for error in self.errors:
            if error.name == name:
                return error
        return None

    def find_resource(self, uri: str) -> ResourceSpec | None:
        for resource in self.resources:
            if resource.uri == uri:
                return resource
        return None
