"""Tool sampling — generate diverse argument sets and execute them against a live server.

``ToolSampler`` produces N argument variations for each tool (using the same
schema-based generator as ``mcptest generate``) and calls the server, recording
both successful responses and error responses without aborting.

The results are ``SampledTool`` objects that :class:`~mcptest.capture.fixture_gen.FixtureGenerator`
consumes to produce fixture YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcptest.generate.values import generate_valid, _value_for_field


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ToolSample:
    """One (args, response) pair recorded during sampling.

    Attributes
    ----------
    args:
        The argument dict passed to the tool.
    response:
        The raw response dict returned by ``ServerUnderTest.call_tool()``.
        Always has a ``content`` key; may also have ``isError`` and
        ``structuredContent``.
    is_error:
        ``True`` when the response indicates an error (``isError`` flag or an
        exception was raised and recorded as a text content block).
    label:
        A short human-readable label distinguishing samples (``"sample-0"``,
        ``"sample-1"``, …).
    """

    args: dict[str, Any]
    response: dict[str, Any]
    is_error: bool
    label: str


@dataclass
class SampledTool:
    """A tool's schema plus all recorded (args, response) pairs.

    Attributes
    ----------
    name:
        Tool name as reported by the server.
    description:
        Tool description as reported by the server.
    input_schema:
        The tool's ``inputSchema`` dict.
    samples:
        Ordered list of :class:`ToolSample` objects — successes first, then
        any error responses.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    samples: list[ToolSample] = field(default_factory=list)

    @property
    def success_samples(self) -> list[ToolSample]:
        return [s for s in self.samples if not s.is_error]

    @property
    def error_samples(self) -> list[ToolSample]:
        return [s for s in self.samples if s.is_error]


# ---------------------------------------------------------------------------
# Argument generation helpers
# ---------------------------------------------------------------------------


def _diverse_args(schema: dict[str, Any], n: int) -> list[dict[str, Any]]:
    """Return up to *n* distinct argument dicts for *schema*.

    Strategy:
    - Sample 0: ``generate_valid()`` — canonical required-fields-only call.
    - Sample 1: all properties populated (not just required ones).
    - Sample 2+: vary individual required fields with alternative values.

    Always returns at least one dict even for empty schemas.
    """
    if n <= 0:
        n = 1

    results: list[dict[str, Any]] = []
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    # Sample 0 — only required fields
    base = generate_valid(schema)
    results.append(base)
    if len(results) >= n:
        return results

    # Sample 1 — all properties
    if properties:
        full = {k: _value_for_field(k, v) for k, v in properties.items()}
        if full != base:
            results.append(full)
    if len(results) >= n:
        return results

    # Sample 2+ — vary each required field in turn
    for field_name in required:
        field_schema = properties.get(field_name, {})
        typ = field_schema.get("type", "string")
        # Pick a value that differs from the base value
        alt = _alt_value(field_name, field_schema, typ, base.get(field_name))
        varied = {**base, field_name: alt}
        if varied not in results:
            results.append(varied)
        if len(results) >= n:
            return results

    return results


def _alt_value(field_name: str, field_schema: dict, typ: str, current: Any) -> Any:
    """Return an alternative valid value different from *current*."""
    if "enum" in field_schema:
        enum_vals = field_schema["enum"]
        # Pick second value if available, else first
        for v in enum_vals:
            if v != current:
                return v
        return current

    if typ == "string":
        return f"alt-{field_name}"
    if typ == "integer":
        return (current or 0) + 1
    if typ == "number":
        return (current or 0.0) + 1.0
    if typ == "boolean":
        return not current if isinstance(current, bool) else False
    if typ == "array":
        return []
    if typ == "object":
        return {}
    return f"alt-{field_name}"


# ---------------------------------------------------------------------------
# ToolSampler
# ---------------------------------------------------------------------------


class ToolSampler:
    """Generate sample arguments and execute them against a live server.

    Parameters
    ----------
    server:
        Any object satisfying the ``ServerUnderTest`` protocol.
    samples_per_tool:
        How many distinct argument sets to try per tool (default: 3).
    """

    def __init__(self, server: Any, samples_per_tool: int = 3) -> None:
        self._server = server
        self._n = samples_per_tool

    def sample_tool(
        self, tool_name: str, schema: dict[str, Any], n: int | None = None
    ) -> list[dict[str, Any]]:
        """Return up to *n* diverse argument dicts for a tool's *schema*.

        This is a pure, synchronous method — no server calls.  Useful for unit
        testing the argument generation in isolation.
        """
        count = n if n is not None else self._n
        return _diverse_args(schema, count)

    async def execute_samples(
        self,
        tool_name: str,
        schema: dict[str, Any],
    ) -> list[ToolSample]:
        """Call the tool N times with diverse args, record each response.

        Errors from the server (``isError: true`` responses, or exceptions
        during ``call_tool``) are recorded as error samples rather than
        raising, so a single failing tool doesn't abort the whole capture.
        """
        arg_sets = _diverse_args(schema, self._n)
        samples: list[ToolSample] = []

        for idx, args in enumerate(arg_sets):
            label = f"sample-{idx}"
            try:
                response = await self._server.call_tool(tool_name, args)
                is_error = bool(response.get("isError", False))
            except Exception as exc:
                response = {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                }
                is_error = True

            samples.append(
                ToolSample(args=args, response=response, is_error=is_error, label=label)
            )

        return samples

    async def sample_all(
        self, tools: list[dict[str, Any]]
    ) -> list[SampledTool]:
        """Sample every tool in *tools* and return :class:`SampledTool` objects.

        Parameters
        ----------
        tools:
            Tool-definition dicts as returned by ``ServerUnderTest.list_tools()``
            — each must have ``name``, ``description``, and ``inputSchema`` keys.
        """
        results: list[SampledTool] = []
        for tool_dict in tools:
            name = tool_dict["name"]
            description = tool_dict.get("description") or ""
            schema: dict[str, Any] = tool_dict.get("inputSchema") or {
                "type": "object",
                "properties": {},
            }
            samples = await self.execute_samples(name, schema)
            results.append(
                SampledTool(
                    name=name,
                    description=description,
                    input_schema=schema,
                    samples=samples,
                )
            )
        return results
