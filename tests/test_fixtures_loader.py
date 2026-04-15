"""Unit tests for YAML fixture loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcptest import Fixture, FixtureLoadError, load_fixture, load_fixtures


GITHUB_FIXTURE = """
server:
  name: mock-github
  version: "1.0"
  description: "GitHub mock"

tools:
  - name: create_issue
    description: "Create a GitHub issue"
    input_schema:
      type: object
      properties:
        repo: { type: string }
        title: { type: string }
        body: { type: string }
      required: [repo, title]
    responses:
      - match: { repo: "acme/api" }
        return:
          issue_number: 42
          url: "https://github.com/acme/api/issues/42"
      - default: true
        return:
          issue_number: 1
          url: "https://github.com/acme/fake/issues/1"

  - name: list_issues
    responses:
      - return:
          issues: []

resources:
  - uri: "github://acme/api/readme"
    content: "# ACME API"

errors:
  - name: rate_limited
    tool: create_issue
    error_code: -32000
    message: "GitHub API rate limit exceeded"
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


class TestLoadFixture:
    def test_minimal(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "mini.yaml",
            "server: { name: x }\n"
            "tools:\n"
            "  - name: ping\n"
            "    responses:\n"
            "      - return: { ok: true }\n",
        )
        fx = load_fixture(p)
        assert isinstance(fx, Fixture)
        assert fx.server.name == "x"
        assert len(fx.tools) == 1

    def test_github_example(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "gh.yaml", GITHUB_FIXTURE)
        fx = load_fixture(p)
        assert fx.server.name == "mock-github"
        assert fx.server.version == "1.0"

        create = fx.find_tool("create_issue")
        assert create is not None
        assert len(create.responses) == 2
        assert create.responses[0].match == {"repo": "acme/api"}
        assert create.responses[0].return_value == {
            "issue_number": 42,
            "url": "https://github.com/acme/api/issues/42",
        }
        assert create.responses[1].default is True

        assert fx.find_error("rate_limited") is not None
        assert fx.find_resource("github://acme/api/readme") is not None

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "s.yaml", "server: { name: s }\n")
        fx = load_fixture(str(p))
        assert fx.server.name == "s"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FixtureLoadError, match="not found"):
            load_fixture(tmp_path / "nonexistent.yaml")

    def test_path_is_directory(self, tmp_path: Path) -> None:
        with pytest.raises(FixtureLoadError, match="not a file"):
            load_fixture(tmp_path)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "bad.yaml", "foo: [unclosed\n")
        with pytest.raises(FixtureLoadError, match="invalid YAML"):
            load_fixture(p)

    def test_empty_file(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "empty.yaml", "")
        with pytest.raises(FixtureLoadError, match="empty"):
            load_fixture(p)

    def test_top_level_list(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "list.yaml", "- a\n- b\n")
        with pytest.raises(FixtureLoadError, match="mapping"):
            load_fixture(p)

    def test_missing_required_field(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "no_server.yaml", "tools: []\n")
        with pytest.raises(FixtureLoadError, match="invalid fixture"):
            load_fixture(p)

    def test_unreadable_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write(tmp_path, "r.yaml", "server: { name: x }\n")

        def fail_read(*args: object, **kwargs: object) -> str:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", fail_read)
        with pytest.raises(FixtureLoadError, match="could not read"):
            load_fixture(p)


class TestLoadFixtures:
    def test_glob(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.yaml", "server: { name: a }\n")
        _write(tmp_path, "b.yaml", "server: { name: b }\n")
        _write(tmp_path, "c.txt", "server: { name: c }\n")

        fxs = load_fixtures([str(tmp_path / "*.yaml")])
        names = sorted(f.server.name for f in fxs)
        assert names == ["a", "b"]

    def test_recursive_glob(self, tmp_path: Path) -> None:
        nested = tmp_path / "sub"
        nested.mkdir()
        _write(tmp_path, "top.yaml", "server: { name: top }\n")
        _write(nested, "deep.yaml", "server: { name: deep }\n")

        fxs = load_fixtures([str(tmp_path / "**" / "*.yaml")])
        names = sorted(f.server.name for f in fxs)
        assert names == ["deep", "top"]

    def test_direct_path(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "x.yaml", "server: { name: x }\n")
        fxs = load_fixtures([str(p)])
        assert len(fxs) == 1
        assert fxs[0].server.name == "x"

    def test_no_matches(self, tmp_path: Path) -> None:
        with pytest.raises(FixtureLoadError, match="no files matched"):
            load_fixtures([str(tmp_path / "ghost_*.yaml")])

    def test_multiple_patterns(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.yaml", "server: { name: a }\n")
        _write(tmp_path, "b.yml", "server: { name: b }\n")
        fxs = load_fixtures(
            [str(tmp_path / "*.yaml"), str(tmp_path / "*.yml")]
        )
        names = sorted(f.server.name for f in fxs)
        assert names == ["a", "b"]

    def test_deduplicates_same_file(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "x.yaml", "server: { name: x }\n")
        fxs = load_fixtures([str(p), str(p), str(tmp_path / "*.yaml")])
        assert len(fxs) == 1

    def test_direct_path_fallback_when_glob_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = _write(tmp_path, "x.yaml", "server: { name: x }\n")

        def empty_glob(*args: object, **kwargs: object) -> list[str]:
            return []

        monkeypatch.setattr("mcptest.fixtures.loader.glob", empty_glob)
        fxs = load_fixtures([str(p)])
        assert len(fxs) == 1
        assert fxs[0].server.name == "x"

    def test_empty_glob_and_missing_path_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def empty_glob(*args: object, **kwargs: object) -> list[str]:
            return []

        monkeypatch.setattr("mcptest.fixtures.loader.glob", empty_glob)
        with pytest.raises(FixtureLoadError, match="no files matched"):
            load_fixtures([str(tmp_path / "ghost.yaml")])
