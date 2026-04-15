"""Pydantic models for the YAML test-file schema."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentSpec(BaseModel):
    """How to invoke the agent under test."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(..., min_length=1)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    timeout_s: float = 60.0
    input_via: str = "stdin"

    def build_adapter(self, base_dir: Path) -> Any:
        """Build a `SubprocessAdapter` bound to `base_dir` for relative paths.

        Imported lazily to avoid a cycle: `runner/adapters.py` is part of the
        public surface and we want `testspec` to be importable standalone.
        """
        from mcptest.runner.adapters import SubprocessAdapter

        parts = shlex.split(self.command)
        if not parts:
            raise ValueError(f"agent command is empty: {self.command!r}")
        command, *args = parts

        # `python` as a shebang-style literal gets rewritten to the current
        # interpreter so tests run in the same venv as mcptest itself,
        # matching how `PythonScriptAdapter` behaves in code.
        if command == "python":
            command = sys.executable

        if self.cwd is not None:
            cwd_path = Path(self.cwd)
            if not cwd_path.is_absolute():
                cwd_path = (base_dir / cwd_path).resolve()
            cwd = str(cwd_path)
        else:
            # Default to the test file's directory so relative paths in the
            # `command` field resolve where authors expect.
            cwd = str(base_dir.resolve())

        return SubprocessAdapter(
            command=command,
            args=args,
            env=dict(self.env),
            cwd=cwd,
            timeout_s=self.timeout_s,
            input_via=self.input_via,
        )


class TestCase(BaseModel):
    """One input/assertion pair inside a test suite."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    input: str = ""
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    inject_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    retry: int = Field(default=1, ge=1)
    tolerance: float = Field(default=1.0, ge=0.0, le=1.0)
    eval: dict[str, Any] | None = None  # inline rubric definition for mcptest eval


class TestSuite(BaseModel):
    """One complete YAML test file."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    fixtures: list[str] = Field(default_factory=list)
    agent: AgentSpec
    cases: list[TestCase] = Field(default_factory=list)
    description: str | None = None
    parallel: bool = True

    @model_validator(mode="after")
    def _has_cases(self) -> TestSuite:
        if not self.cases:
            raise ValueError(
                f"test suite {self.name!r} must define at least one case"
            )
        return self

    def resolve_fixtures(self, base_dir: Path) -> list[str]:
        """Resolve each fixture reference relative to the test file."""
        resolved: list[str] = []
        for f in self.fixtures:
            p = Path(f)
            if not p.is_absolute():
                p = (base_dir / p).resolve()
            resolved.append(str(p))
        return resolved
