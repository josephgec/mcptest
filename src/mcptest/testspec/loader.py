"""YAML loader for test suite files."""

from __future__ import annotations

from glob import glob
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError

from mcptest.testspec.models import TestSuite


class TestSuiteLoadError(Exception):
    """Raised when a test file cannot be loaded or validated."""


def load_test_suite(path: str | Path) -> TestSuite:
    p = Path(path)
    if not p.exists():
        raise TestSuiteLoadError(f"test file not found: {p}")
    if not p.is_file():
        raise TestSuiteLoadError(f"test path is not a file: {p}")

    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        raise TestSuiteLoadError(f"could not read test file {p}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TestSuiteLoadError(f"invalid YAML in {p}: {exc}") from exc

    if data is None:
        raise TestSuiteLoadError(f"test file {p} is empty")
    if not isinstance(data, dict):
        raise TestSuiteLoadError(
            f"test file {p} must be a mapping at the top level, got {type(data).__name__}"
        )

    try:
        return TestSuite.model_validate(data)
    except ValidationError as exc:
        raise TestSuiteLoadError(f"invalid test file {p}: {exc}") from exc


def load_test_suites(patterns: Iterable[str | Path]) -> list[tuple[Path, TestSuite]]:
    """Load multiple test files and return `(path, suite)` pairs."""
    suites: list[tuple[Path, TestSuite]] = []
    seen: set[Path] = set()

    for pattern in patterns:
        pattern_str = str(pattern)
        matched = sorted(glob(pattern_str, recursive=True))

        if not matched:
            direct = Path(pattern_str)
            if direct.exists():
                matched = [str(direct)]
            else:
                raise TestSuiteLoadError(
                    f"no files matched pattern: {pattern_str}"
                )

        for m in matched:
            resolved = Path(m).resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            suites.append((resolved, load_test_suite(resolved)))

    return suites


def discover_test_files(root: str | Path) -> list[Path]:
    """Find test files under `root`.

    - If `root` is a file, return it.
    - Otherwise glob for YAML files matching `test_*.yaml`, `test_*.yml`,
      `*_test.yaml`, or `*_test.yml`. Returned in deterministic sort order.
    """
    p = Path(root)
    if p.is_file():
        return [p]
    if not p.exists():
        return []
    patterns = ["test_*.yaml", "test_*.yml", "*_test.yaml", "*_test.yml"]
    out: list[Path] = []
    for pat in patterns:
        out.extend(sorted(p.glob(f"**/{pat}")))
    # De-duplicate while preserving first-seen order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for m in out:
        if m in seen:
            continue
        seen.add(m)
        unique.append(m)
    return unique
