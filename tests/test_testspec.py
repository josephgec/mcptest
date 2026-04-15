"""Unit tests for the test-file loader and models."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mcptest.testspec import (
    AgentSpec,
    load_test_suite,
    load_test_suites,
)
from mcptest.testspec.models import TestCase as _TestCase
from mcptest.testspec.models import TestSuite as _TestSuite
from mcptest.testspec.loader import TestSuiteLoadError as _TestSuiteLoadError
from mcptest.testspec.loader import discover_test_files


_MINIMAL = """\
name: basic
fixtures:
  - fixtures/x.yaml
agent:
  command: python my_agent.py
cases:
  - name: one
    input: hi
    assertions:
      - tool_called: x
"""


class TestModels:
    def test_testcase_minimal(self) -> None:
        c = _TestCase(name="x")
        assert c.input == ""
        assert c.assertions == []

    def test_testcase_rejects_empty_name(self) -> None:
        with pytest.raises(ValidationError):
            _TestCase(name="")

    def test_suite_requires_cases(self) -> None:
        with pytest.raises(ValidationError, match="at least one case"):
            _TestSuite(
                name="s",
                fixtures=[],
                agent=AgentSpec(command="python x.py"),
                cases=[],
            )

    def test_agentspec_rejects_empty_command(self) -> None:
        with pytest.raises(ValidationError):
            AgentSpec(command="")

    def test_resolve_fixtures_relative(self, tmp_path: Path) -> None:
        suite = _TestSuite(
            name="s",
            fixtures=["fixtures/a.yaml", "/abs/b.yaml"],
            agent=AgentSpec(command="x"),
            cases=[_TestCase(name="c")],
        )
        resolved = suite.resolve_fixtures(tmp_path)
        assert resolved[0] == str((tmp_path / "fixtures" / "a.yaml").resolve())
        assert resolved[1] == "/abs/b.yaml"

    def test_build_adapter_python_rewritten(self, tmp_path: Path) -> None:
        import sys

        spec = AgentSpec(command="python my_agent.py --flag")
        adapter = spec.build_adapter(tmp_path)
        assert adapter.command == sys.executable
        assert adapter.args == ["my_agent.py", "--flag"]

    def test_build_adapter_non_python(self, tmp_path: Path) -> None:
        spec = AgentSpec(command="/bin/echo hello")
        adapter = spec.build_adapter(tmp_path)
        assert adapter.command == "/bin/echo"
        assert adapter.args == ["hello"]

    def test_build_adapter_empty_rejected(self, tmp_path: Path) -> None:
        spec = AgentSpec.model_construct(command="   ")
        with pytest.raises(ValueError, match="empty"):
            spec.build_adapter(tmp_path)

    def test_build_adapter_cwd_relative(self, tmp_path: Path) -> None:
        spec = AgentSpec(command="x", cwd="sub")
        adapter = spec.build_adapter(tmp_path)
        assert adapter.cwd == str((tmp_path / "sub").resolve())

    def test_build_adapter_cwd_absolute(self, tmp_path: Path) -> None:
        spec = AgentSpec(command="x", cwd=str(tmp_path))
        adapter = spec.build_adapter(tmp_path)
        assert adapter.cwd == str(tmp_path)


class TestLoader:
    def test_minimal(self, tmp_path: Path) -> None:
        p = tmp_path / "t.yaml"
        p.write_text(_MINIMAL)
        suite = load_test_suite(p)
        assert suite.name == "basic"
        assert len(suite.cases) == 1

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(_TestSuiteLoadError, match="not found"):
            load_test_suite(tmp_path / "nope.yaml")

    def test_not_a_file(self, tmp_path: Path) -> None:
        with pytest.raises(_TestSuiteLoadError, match="not a file"):
            load_test_suite(tmp_path)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("[unclosed\n")
        with pytest.raises(_TestSuiteLoadError, match="invalid YAML"):
            load_test_suite(p)

    def test_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("")
        with pytest.raises(_TestSuiteLoadError, match="empty"):
            load_test_suite(p)

    def test_top_level_not_mapping(self, tmp_path: Path) -> None:
        p = tmp_path / "l.yaml"
        p.write_text("- a\n- b\n")
        with pytest.raises(_TestSuiteLoadError, match="mapping"):
            load_test_suite(p)

    def test_schema_violation(self, tmp_path: Path) -> None:
        p = tmp_path / "s.yaml"
        p.write_text("name: x\nagent:\n  command: y\ncases: []\n")
        with pytest.raises(_TestSuiteLoadError, match="at least one case"):
            load_test_suite(p)

    def test_load_test_suites_glob(self, tmp_path: Path) -> None:
        (tmp_path / "test_a.yaml").write_text(_MINIMAL.replace("basic", "a"))
        (tmp_path / "test_b.yaml").write_text(_MINIMAL.replace("basic", "b"))
        suites = load_test_suites([str(tmp_path / "test_*.yaml")])
        names = sorted(s.name for _, s in suites)
        assert names == ["a", "b"]

    def test_load_test_suites_no_match(self, tmp_path: Path) -> None:
        with pytest.raises(_TestSuiteLoadError, match="no files matched"):
            load_test_suites([str(tmp_path / "ghost_*.yaml")])

    def test_load_test_suites_direct_path(self, tmp_path: Path) -> None:
        p = tmp_path / "t.yaml"
        p.write_text(_MINIMAL)
        suites = load_test_suites([str(p)])
        assert len(suites) == 1

    def test_load_test_suites_dedup(self, tmp_path: Path) -> None:
        p = tmp_path / "t.yaml"
        p.write_text(_MINIMAL)
        suites = load_test_suites([str(p), str(p)])
        assert len(suites) == 1

    def test_load_test_suites_direct_path_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "t.yaml"
        p.write_text(_MINIMAL)

        def empty_glob(*args: object, **kwargs: object) -> list[str]:
            return []

        monkeypatch.setattr("mcptest.testspec.loader.glob", empty_glob)
        suites = load_test_suites([str(p)])
        assert len(suites) == 1


class TestDiscover:
    def test_file_argument(self, tmp_path: Path) -> None:
        p = tmp_path / "t.yaml"
        p.write_text(_MINIMAL)
        assert discover_test_files(p) == [p]

    def test_missing_directory(self, tmp_path: Path) -> None:
        assert discover_test_files(tmp_path / "nope") == []

    def test_directory_with_test_files(self, tmp_path: Path) -> None:
        (tmp_path / "test_a.yaml").write_text(_MINIMAL)
        (tmp_path / "b_test.yml").write_text(_MINIMAL)
        (tmp_path / "ignored.yaml").write_text(_MINIMAL)
        nested = tmp_path / "sub"
        nested.mkdir()
        (nested / "test_c.yaml").write_text(_MINIMAL)

        files = discover_test_files(tmp_path)
        names = {p.name for p in files}
        assert "test_a.yaml" in names
        assert "b_test.yml" in names
        assert "test_c.yaml" in names
        assert "ignored.yaml" not in names

    def test_directory_empty(self, tmp_path: Path) -> None:
        assert discover_test_files(tmp_path) == []

    def test_dedupes_across_patterns(self, tmp_path: Path) -> None:
        p = tmp_path / "test_x_test.yaml"  # matches both patterns
        p.write_text(_MINIMAL)
        files = discover_test_files(tmp_path)
        assert files == [p]
