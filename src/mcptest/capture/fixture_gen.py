"""Fixture and test-spec YAML generation from live-captured server data.

:class:`FixtureGenerator` converts a :class:`~mcptest.capture.discovery.DiscoveryResult`
and a list of :class:`~mcptest.capture.sampler.SampledTool` objects into the
mcptest fixture YAML format.  The generated fixture is immediately loadable by
:func:`~mcptest.fixtures.loader.load_fixture`.

Additionally, ``generate_tests()`` delegates to the existing
:func:`~mcptest.generate.engine.generate_suite` engine to produce test-spec
YAML for the generated fixture.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from mcptest.capture.discovery import DiscoveryResult
from mcptest.capture.sampler import SampledTool


# ---------------------------------------------------------------------------
# FixtureGenerator
# ---------------------------------------------------------------------------


class FixtureGenerator:
    """Build fixture and test-spec YAML from discovery + sampled responses.

    Parameters
    ----------
    discovery:
        Result of :meth:`~mcptest.capture.discovery.ServerDiscovery.discover`.
    samples:
        List of :class:`~mcptest.capture.sampler.SampledTool` objects from
        :meth:`~mcptest.capture.sampler.ToolSampler.sample_all`.
    """

    def __init__(self, discovery: DiscoveryResult, samples: list[SampledTool]) -> None:
        self._discovery = discovery
        self._samples = samples

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate_fixture(self) -> str:
        """Return a YAML string representing the captured fixture.

        The output is valid mcptest fixture YAML — it can be written to a file
        and loaded immediately with :func:`~mcptest.fixtures.loader.load_fixture`.
        """
        data = self._build_fixture_dict()
        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def generate_fixture_dict(self) -> dict[str, Any]:
        """Return the fixture as a plain dict (useful for programmatic use)."""
        return self._build_fixture_dict()

    def generate_tests(
        self,
        fixture_path: str,
        agent_cmd: str = "python agent.py",
        *,
        categories: list[str] | None = None,
        timeout_s: float = 60.0,
    ) -> str:
        """Return a YAML string representing a generated test suite.

        Delegates to :func:`~mcptest.generate.engine.generate_suite` using the
        fixture built from this capture session.

        Parameters
        ----------
        fixture_path:
            Relative path to embed in the ``fixtures:`` list (e.g.
            ``"fixtures/myserver.yaml"``).
        agent_cmd:
            Agent command to embed in the suite (placeholder — callers can
            replace it after writing the file).
        categories:
            Subset of test categories to generate (defaults to all six).
        timeout_s:
            Per-case agent timeout.
        """
        from mcptest.fixtures.loader import load_fixture as _load_fixture
        from mcptest.fixtures.models import Fixture
        from mcptest.generate.engine import generate_suite

        fixture_dict = self._build_fixture_dict()

        # Validate the fixture dict by round-tripping through Pydantic.
        fixture = Fixture.model_validate(fixture_dict)

        suite_name = _slugify(self._discovery.server_name) + "-captured"
        suite_dict = generate_suite(
            [fixture],
            name=suite_name,
            agent_cmd=agent_cmd,
            categories=categories,
            timeout_s=timeout_s,
            fixture_paths=[fixture_path],
        )
        return yaml.dump(suite_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # ------------------------------------------------------------------
    # Private builders
    # ------------------------------------------------------------------

    def _build_fixture_dict(self) -> dict[str, Any]:
        """Assemble the full fixture dict from discovery + samples."""
        d: dict[str, Any] = {
            "server": {
                "name": self._discovery.server_name or "captured-server",
                "version": self._discovery.server_version or "0.1.0",
            },
        }

        tools = self._build_tools()
        if tools:
            d["tools"] = tools

        resources = self._build_resources()
        if resources:
            d["resources"] = resources

        errors = self._build_errors()
        if errors:
            d["errors"] = errors

        return d

    def _build_tools(self) -> list[dict[str, Any]]:
        """Build the ``tools:`` list from sampled tools."""
        tools = []
        for sampled in self._samples:
            tool_dict: dict[str, Any] = {
                "name": sampled.name,
                "description": sampled.description or f"Captured tool: {sampled.name}",
                "input_schema": sampled.input_schema,
            }

            responses = self._build_responses(sampled)
            if responses:
                tool_dict["responses"] = responses
            else:
                # Fixture validator requires at least one response
                tool_dict["responses"] = [{"default": True, "return_text": "ok"}]

            tools.append(tool_dict)
        return tools

    def _build_responses(self, sampled: SampledTool) -> list[dict[str, Any]]:
        """Build the ``responses:`` list for one tool from its samples.

        Strategy:
        - Success samples with non-empty structured content → ``return:`` entry
          with the content as the return value, no match condition (first
          success becomes the default).
        - Success samples with only text content → ``return_text:`` entry.
        - Error samples → recorded as comments / ignored (errors become entries
          in the fixture's ``errors:`` list, not inline response entries).
        """
        responses: list[dict[str, Any]] = []
        seen_contents: set[str] = set()
        made_default = False

        for sample in sampled.success_samples:
            resp_entry = self._response_entry_for(sample.args, sample.response)
            if resp_entry is None:
                continue

            # Deduplicate by serialised response body
            key = _stable_key(resp_entry)
            if key in seen_contents:
                continue
            seen_contents.add(key)

            if not made_default:
                # The first success response is the default fallback
                resp_entry["default"] = True
                made_default = True

            responses.append(resp_entry)

        # If no successes, fall back to a plain text default
        if not responses:
            responses = [{"default": True, "return_text": "ok"}]

        return responses

    def _response_entry_for(
        self, args: dict[str, Any], response: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Convert a raw server response to a fixture response entry dict."""
        structured = response.get("structuredContent")
        content_blocks = response.get("content", [])

        if structured is not None and isinstance(structured, dict):
            return {"return": structured}

        # Extract text from content blocks
        texts = [
            b.get("text", "")
            for b in content_blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        text = "\n".join(t for t in texts if t)
        if text:
            return {"return_text": text}

        return None

    def _build_resources(self) -> list[dict[str, Any]]:
        """Build the ``resources:`` list from discovered resources."""
        resources = []
        for res in self._discovery.resources:
            entry: dict[str, Any] = {
                "uri": res.get("uri", ""),
                "content": "",  # Content not sampled at discovery time
            }
            if res.get("name"):
                entry["name"] = res["name"]
            if res.get("description"):
                entry["description"] = res["description"]
            if res.get("mimeType"):
                entry["mime_type"] = res["mimeType"]
            resources.append(entry)
        return resources

    def _build_errors(self) -> list[dict[str, Any]]:
        """Build the ``errors:`` list from error samples across all tools."""
        errors: list[dict[str, Any]] = []
        seen_messages: set[str] = set()
        error_index = 0

        for sampled in self._samples:
            for sample in sampled.error_samples:
                # Extract error message from content blocks
                msg = _extract_text(sample.response)
                if not msg:
                    msg = "tool call failed"

                # Deduplicate by first 80 chars of message
                key = msg[:80]
                if key in seen_messages:
                    continue
                seen_messages.add(key)

                error_name = f"error-{_slugify(sampled.name)}-{error_index}"
                error_index += 1
                errors.append(
                    {
                        "name": error_name,
                        "tool": sampled.name,
                        "message": msg,
                    }
                )

        return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text(response: dict[str, Any]) -> str:
    """Pull the first text block from a response content list."""
    for block in response.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "")
    return ""


def _stable_key(d: dict[str, Any]) -> str:
    """A stable string key for deduplication — ignores ``default`` flag."""
    without_default = {k: v for k, v in d.items() if k != "default"}
    return repr(sorted(without_default.items()))


def _slugify(s: str) -> str:
    """Convert *s* to a lowercase hyphen-slug, truncated to 40 chars."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return slug[:40] or "server"
