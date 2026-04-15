"""End-to-end orchestration for ``mcptest capture``.

:func:`capture_server` is the single entry point for the capture workflow:

1. **Connect** — create and connect a ``StdioServer`` (or accept any
   ``ServerUnderTest`` for testing).
2. **Discover** — enumerate capabilities, tools, and resources.
3. **Sample** — generate diverse arguments and execute them against the server.
4. **Generate** — convert discovery + samples into fixture YAML (and optionally
   test-spec YAML).
5. **Write** — save generated files to the output directory.

The function returns a :class:`CaptureResult` that records what was produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcptest.capture.discovery import DiscoveryResult, ServerDiscovery
from mcptest.capture.fixture_gen import FixtureGenerator, _slugify
from mcptest.capture.sampler import SampledTool, ToolSampler


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class CaptureResult:
    """Everything produced by a single capture run.

    Attributes
    ----------
    fixture_path:
        Path of the written fixture YAML, or ``None`` in dry-run mode.
    test_paths:
        List of paths for written test-spec YAML files (empty unless
        ``generate_tests=True`` was requested).
    discovery:
        The :class:`~mcptest.capture.discovery.DiscoveryResult` from the session.
    sampled_tools:
        The :class:`~mcptest.capture.sampler.SampledTool` list from sampling.
    sample_count:
        Total number of tool calls executed (across all tools).
    dry_run:
        ``True`` when files were not written.
    """

    fixture_path: Path | None
    test_paths: list[Path]
    discovery: DiscoveryResult
    sampled_tools: list[SampledTool]
    sample_count: int
    dry_run: bool = False

    @property
    def tool_count(self) -> int:
        return len(self.sampled_tools)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def capture_server(
    server_or_command: Any,
    output_dir: str | Path = ".",
    *,
    generate_tests: bool = False,
    samples_per_tool: int = 3,
    dry_run: bool = False,
    agent_cmd: str = "python agent.py",
) -> CaptureResult:
    """Connect to a server, sample its tools, and write fixture/test files.

    Parameters
    ----------
    server_or_command:
        Either a shell command string (``"python my_server.py"``) that will be
        parsed and connected via ``StdioServer``, or any object already
        satisfying the ``ServerUnderTest`` protocol (used directly — no
        connection step performed).  Passing a protocol object is the preferred
        approach for testing.
    output_dir:
        Directory where generated files are written.  Created if absent.
    generate_tests:
        If ``True``, also generate and write a test-spec YAML file alongside
        the fixture.
    samples_per_tool:
        How many argument variations to try per tool.
    dry_run:
        If ``True``, perform discovery and sampling but do not write any files.
        The returned :class:`CaptureResult` has ``fixture_path=None``.
    agent_cmd:
        Agent command string embedded in generated test suites.

    Returns
    -------
    CaptureResult
    """
    out = Path(output_dir)

    # ------------------------------------------------------------------
    # 1. Connect (only for string commands)
    # ------------------------------------------------------------------
    server = server_or_command
    owns_server = False

    if isinstance(server_or_command, str):
        from mcptest.conformance.server import make_stdio_server

        server = make_stdio_server(server_or_command)
        await server.connect()
        owns_server = True

    try:
        # ------------------------------------------------------------------
        # 2. Discover
        # ------------------------------------------------------------------
        discovery = await ServerDiscovery(server).discover()

        # ------------------------------------------------------------------
        # 3. Sample
        # ------------------------------------------------------------------
        sampler = ToolSampler(server, samples_per_tool=samples_per_tool)
        sampled_tools = await sampler.sample_all(discovery.tools)
        sample_count = sum(len(st.samples) for st in sampled_tools)

        # ------------------------------------------------------------------
        # 4. Generate YAML
        # ------------------------------------------------------------------
        gen = FixtureGenerator(discovery, sampled_tools)
        fixture_yaml = gen.generate_fixture()

        server_slug = _slugify(discovery.server_name) or "captured"
        fixture_filename = f"{server_slug}.yaml"

        test_yaml: str | None = None
        test_filename: str | None = None
        if generate_tests:
            fixture_rel_path = f"fixtures/{fixture_filename}"
            test_yaml = gen.generate_tests(
                fixture_path=fixture_rel_path,
                agent_cmd=agent_cmd,
            )
            test_filename = f"{server_slug}-tests.yaml"

        # ------------------------------------------------------------------
        # 5. Write files
        # ------------------------------------------------------------------
        if dry_run:
            return CaptureResult(
                fixture_path=None,
                test_paths=[],
                discovery=discovery,
                sampled_tools=sampled_tools,
                sample_count=sample_count,
                dry_run=True,
            )

        out.mkdir(parents=True, exist_ok=True)
        fixture_path = out / fixture_filename
        fixture_path.write_text(fixture_yaml, encoding="utf-8")

        test_paths: list[Path] = []
        if test_yaml is not None and test_filename is not None:
            test_path = out / test_filename
            test_path.write_text(test_yaml, encoding="utf-8")
            test_paths.append(test_path)

        return CaptureResult(
            fixture_path=fixture_path,
            test_paths=test_paths,
            discovery=discovery,
            sampled_tools=sampled_tools,
            sample_count=sample_count,
            dry_run=False,
        )

    finally:
        if owns_server:
            await server.close()
