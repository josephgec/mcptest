"""YAML loader for mcptest fixture files.

Accepts individual paths or glob patterns and returns parsed, validated
`Fixture` objects. All errors funnel through `FixtureLoadError` so callers can
present a single message regardless of the failure mode (missing file, bad
YAML, schema validation).
"""

from __future__ import annotations

from glob import glob
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError

from mcptest.fixtures.models import Fixture


class FixtureLoadError(Exception):
    """Raised when a fixture file cannot be loaded or validated."""


def load_fixture(path: str | Path) -> Fixture:
    """Load and validate a single fixture file."""
    p = Path(path)
    if not p.exists():
        raise FixtureLoadError(f"fixture file not found: {p}")
    if not p.is_file():
        raise FixtureLoadError(f"fixture path is not a file: {p}")

    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise FixtureLoadError(f"could not read fixture {p}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FixtureLoadError(f"invalid YAML in {p}: {exc}") from exc

    if data is None:
        raise FixtureLoadError(f"fixture {p} is empty")
    if not isinstance(data, dict):
        raise FixtureLoadError(
            f"fixture {p} must be a mapping at the top level, got {type(data).__name__}"
        )

    try:
        return Fixture.model_validate(data)
    except ValidationError as exc:
        raise FixtureLoadError(f"invalid fixture {p}: {exc}") from exc


def load_fixtures(patterns: Iterable[str | Path]) -> list[Fixture]:
    """Load one or more fixtures from a list of paths and/or glob patterns.

    Each pattern is expanded with `glob(..., recursive=True)`. A pattern that
    resolves to no files raises `FixtureLoadError` — silent no-ops tend to
    mask typos in test configuration.
    """
    fixtures: list[Fixture] = []
    seen: set[Path] = set()

    for pattern in patterns:
        pattern_str = str(pattern)
        matched = sorted(glob(pattern_str, recursive=True))

        if not matched:
            # Fall back to treating the pattern as a literal path so absolute
            # paths without glob characters still work.
            direct = Path(pattern_str)
            if direct.exists():
                matched = [str(direct)]
            else:
                raise FixtureLoadError(f"no files matched pattern: {pattern_str}")

        for m in matched:
            resolved = Path(m).resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            fixtures.append(load_fixture(resolved))

    return fixtures
