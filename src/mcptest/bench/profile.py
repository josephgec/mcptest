"""Agent profile model for mcptest bench.

An *agent profile* describes one agent variant to include in a benchmark run:
its name, the command used to invoke it, optional extra environment variables,
and run-level settings such as retry count and tolerance.

YAML format (top-level ``agents:`` list in ``mcptest.yaml`` or a standalone
profiles file)::

    agents:
      - name: claude-sonnet
        command: python agents/claude.py
        env:
          MODEL: claude-3-5-sonnet-20241022
        description: Anthropic Claude Sonnet 3.5

      - name: gpt-4o
        command: python agents/openai.py
        env:
          MODEL: gpt-4o
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from mcptest.config import McpTestConfig


@dataclass
class AgentProfile:
    """Definition of one agent variant to include in a benchmark run.

    Attributes:
        name: Unique identifier shown in reports and comparison tables.
        command: Shell command used to invoke the agent.  The string is
            split on whitespace — first token is the executable, remaining
            tokens are arguments.
        env: Extra environment variables merged over ``os.environ``.
        description: Human-readable label shown in help output.
        retry: Number of times to run each test case (1 = single run).
        tolerance: Fraction of retries that must pass for a case to be
            considered passing (0.0–1.0).
    """

    name: str
    command: str
    env: dict[str, str] = field(default_factory=dict)
    description: str = ""
    retry: int = 1
    tolerance: float = 1.0


def _profile_from_dict(data: Any) -> AgentProfile:
    """Parse one agent profile from a raw YAML dict."""
    if not isinstance(data, dict):
        raise ValueError(
            f"agent profile must be a mapping, got {type(data).__name__}"
        )
    if "name" not in data:
        raise ValueError("agent profile missing required field: name")
    if "command" not in data:
        raise ValueError(
            f"agent profile {data.get('name')!r} missing required field: command"
        )

    env = data.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError(
            f"agent profile {data['name']!r}: env must be a mapping"
        )

    return AgentProfile(
        name=str(data["name"]),
        command=str(data["command"]),
        env={str(k): str(v) for k, v in env.items()},
        description=str(data.get("description", "")),
        retry=int(data.get("retry", 1)),
        tolerance=float(data.get("tolerance", 1.0)),
    )


def load_profiles(path: Path) -> list[AgentProfile]:
    """Load agent profiles from a YAML file.

    The file may be structured as a top-level ``agents:`` mapping::

        agents:
          - name: agent-a
            command: python a.py

    or as a bare list at the root::

        - name: agent-a
          command: python a.py

    Returns an empty list when the file contains no agent entries.

    Raises:
        ValueError: When the file structure is not a mapping or list.
        yaml.YAMLError: When the file is not valid YAML.
        FileNotFoundError: When *path* does not exist.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if isinstance(raw, dict):
        agents_data = raw.get("agents") or []
    elif isinstance(raw, list):
        agents_data = raw
    else:
        raise ValueError(
            f"profiles file {path} must contain a mapping or list, "
            f"got {type(raw).__name__}"
        )

    return [_profile_from_dict(a) for a in agents_data]


def load_profiles_from_config(config: McpTestConfig) -> list[AgentProfile]:
    """Extract agent profiles from a :class:`~mcptest.config.McpTestConfig`.

    Reads the ``agents`` field (populated from the ``agents:`` section of
    ``mcptest.yaml``) and converts each raw dict into an
    :class:`AgentProfile`.
    """
    return [_profile_from_dict(a) for a in config.agents]
