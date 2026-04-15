"""Project-level configuration loader for mcptest.

Discovers and parses ``mcptest.yaml`` by walking up from CWD (similar to
how ``.gitignore`` discovery works).  CLI flags always override config-file
values — :func:`merge_cli_overrides` handles the precedence.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_CONFIG_FILENAMES = ("mcptest.yaml", "mcptest.yml")


@dataclass
class McpTestConfig:
    """Resolved project configuration.

    All fields default to ``None`` which means "not configured" — callers
    should check for ``None`` before using a value and fall back to their
    own defaults.  Only explicitly set keys in the config file will be
    non-None, which lets CLI flags safely override them (``None`` ≠ default).
    """

    # Paths
    test_paths: list[str] | None = None
    fixture_paths: list[str] | None = None
    baseline_dir: str | None = None

    # Execution
    retry: int | None = None
    tolerance: float | None = None
    parallel: int | None = None
    fail_fast: bool | None = None
    fail_under: float | None = None

    # Metric thresholds for scorecard
    thresholds: dict[str, float] = field(default_factory=dict)

    # Plugin loading
    plugins: list[str] = field(default_factory=list)

    # Cloud settings
    cloud_url: str | None = None
    cloud_api_key_env: str | None = None

    # Agent profiles for benchmarking (mcptest bench)
    agents: list[dict[str, Any]] = field(default_factory=list)

    # Internal: path to the config file that was loaded (None = no file found)
    config_file: Path | None = field(default=None, repr=False)


def find_config_file(start_dir: Path | None = None) -> Path | None:
    """Walk up the directory tree looking for ``mcptest.yaml`` / ``.yml``.

    Starts at *start_dir* (defaults to ``Path.cwd()``) and stops at the
    filesystem root.  Returns the first match found, or ``None``.
    """
    directory = (start_dir or Path.cwd()).resolve()
    while True:
        for name in _CONFIG_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        parent = directory.parent
        if parent == directory:
            # Reached filesystem root.
            break
        directory = parent
    return None


def load_config(path: Path | str | None = None) -> McpTestConfig:
    """Load and parse a config file.

    If *path* is given it must point directly at a ``mcptest.yaml`` file.
    If *path* is ``None`` the function calls :func:`find_config_file` to
    discover one automatically.  Returns an all-defaults
    :class:`McpTestConfig` when no file is found.
    """
    config_path: Path | None
    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(f"Config file not found: {config_path}")
    else:
        config_path = find_config_file()

    if config_path is None:
        return McpTestConfig()

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"Config file {config_path} must contain a YAML mapping, "
            f"got {type(raw).__name__}"
        )

    return _parse_config(raw, config_path)


def _parse_config(raw: dict[str, Any], config_path: Path) -> McpTestConfig:
    """Convert a raw YAML dict into a :class:`McpTestConfig`.

    Unknown keys are silently ignored so future versions can add fields
    without breaking older installs.
    """
    cfg = McpTestConfig(config_file=config_path)

    if "test_paths" in raw:
        cfg.test_paths = [str(p) for p in _as_list(raw["test_paths"])]
    if "fixture_paths" in raw:
        cfg.fixture_paths = [str(p) for p in _as_list(raw["fixture_paths"])]
    if "baseline_dir" in raw:
        cfg.baseline_dir = str(raw["baseline_dir"])
    if "retry" in raw:
        cfg.retry = int(raw["retry"])
    if "tolerance" in raw:
        cfg.tolerance = float(raw["tolerance"])
    if "parallel" in raw:
        cfg.parallel = int(raw["parallel"])
    if "fail_fast" in raw:
        cfg.fail_fast = bool(raw["fail_fast"])
    if "fail_under" in raw:
        cfg.fail_under = float(raw["fail_under"])
    if "thresholds" in raw:
        raw_thresh = raw["thresholds"]
        if isinstance(raw_thresh, dict):
            cfg.thresholds = {k: float(v) for k, v in raw_thresh.items()}
    if "plugins" in raw:
        cfg.plugins = [str(p) for p in _as_list(raw["plugins"])]
    if "agents" in raw:
        raw_agents = raw["agents"]
        if isinstance(raw_agents, list):
            cfg.agents = [dict(a) for a in raw_agents if isinstance(a, dict)]
    if "cloud" in raw:
        cloud = raw["cloud"] or {}
        if isinstance(cloud, dict):
            if "url" in cloud:
                cfg.cloud_url = str(cloud["url"])
            if "api_key_env" in cloud:
                cfg.cloud_api_key_env = str(cloud["api_key_env"])

    return cfg


def _as_list(value: Any) -> list[Any]:
    """Normalise a scalar or list to a list."""
    if isinstance(value, list):
        return value
    return [value]


def merge_cli_overrides(config: McpTestConfig, **kwargs: Any) -> McpTestConfig:
    """Return a new :class:`McpTestConfig` with CLI values layered on top.

    Only non-``None`` kwargs replace the corresponding config field.  This
    means ``--retry 3`` on the CLI wins over ``retry: 2`` in the config
    file, but omitting ``--retry`` leaves the config-file value intact.
    """
    updates: dict[str, Any] = {k: v for k, v in kwargs.items() if v is not None}
    if not updates:
        return config
    return dataclasses.replace(config, **updates)
