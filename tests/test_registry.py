"""Tests for the built-in test-pack registry and install-pack CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from mcptest.cli.main import main as cli_main
from mcptest.fixtures.loader import load_fixture
from mcptest.registry import InstallError, PACKS, get_pack, install_pack, list_packs
from mcptest.registry import TestPack as _TestPack
from mcptest.testspec.loader import load_test_suite


EXPECTED_PACKS = {"filesystem", "database", "http", "git", "slack"}


class TestRegistry:
    def test_lists_all_five_packs(self) -> None:
        assert set(list_packs()) == EXPECTED_PACKS

    def test_get_pack_known(self) -> None:
        pack = get_pack("filesystem")
        assert isinstance(pack, _TestPack)
        assert pack.name == "filesystem"
        assert "fs_read" in pack.files["fixtures/filesystem.yaml"]

    def test_get_pack_unknown_raises(self) -> None:
        with pytest.raises(InstallError, match="unknown pack"):
            get_pack("does-not-exist")

    def test_every_pack_has_fixture_and_tests(self) -> None:
        for name in list_packs():
            pack = PACKS[name]
            assert f"fixtures/{name}.yaml" in pack.files
            assert f"tests/test_{name}.yaml" in pack.files
            assert pack.description


class TestPackContents:
    """Every shipped fixture and test file must parse and round-trip cleanly.

    This catches regressions where someone edits a pack fixture but breaks
    the YAML schema it's supposed to exercise.
    """

    @pytest.mark.parametrize("pack_name", sorted(EXPECTED_PACKS))
    def test_fixture_parses(self, pack_name: str, tmp_path: Path) -> None:
        install_pack(pack_name, tmp_path)
        fx = load_fixture(tmp_path / "fixtures" / f"{pack_name}.yaml")
        assert fx.tools  # every pack exposes at least one tool

    @pytest.mark.parametrize("pack_name", sorted(EXPECTED_PACKS))
    def test_testfile_parses(self, pack_name: str, tmp_path: Path) -> None:
        install_pack(pack_name, tmp_path)
        suite = load_test_suite(tmp_path / "tests" / f"test_{pack_name}.yaml")
        assert suite.cases  # at least one case


class TestInstallPack:
    def test_installs_files(self, tmp_path: Path) -> None:
        written = install_pack("filesystem", tmp_path)
        assert "fixtures/filesystem.yaml" in written
        assert "tests/test_filesystem.yaml" in written
        assert (tmp_path / "fixtures" / "filesystem.yaml").exists()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        dest = tmp_path / "nested" / "deep"
        install_pack("slack", dest)
        assert (dest / "fixtures" / "slack.yaml").exists()

    def test_conflict_without_force(self, tmp_path: Path) -> None:
        install_pack("git", tmp_path)
        with pytest.raises(InstallError, match="already exists"):
            install_pack("git", tmp_path)

    def test_conflict_with_force(self, tmp_path: Path) -> None:
        install_pack("git", tmp_path)
        install_pack("git", tmp_path, force=True)  # no raise
        assert (tmp_path / "fixtures" / "git.yaml").exists()

    def test_unknown_pack(self, tmp_path: Path) -> None:
        with pytest.raises(InstallError, match="unknown pack"):
            install_pack("unknown", tmp_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestListPacksCli:
    def test_list_packs(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli_main, ["list-packs"])
        assert result.exit_code == 0
        for name in EXPECTED_PACKS:
            assert name in result.output


class TestInstallPackCli:
    def test_install_success(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_main, ["install-pack", "http", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "installed pack" in result.output
        assert (tmp_path / "fixtures" / "http.yaml").exists()

    def test_install_unknown_pack(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_main, ["install-pack", "nope", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "unknown pack" in result.output

    def test_install_conflict_without_force(self, tmp_path: Path) -> None:
        runner = CliRunner()
        runner.invoke(cli_main, ["install-pack", "database", str(tmp_path)])
        result = runner.invoke(
            cli_main, ["install-pack", "database", str(tmp_path)]
        )
        assert result.exit_code == 1

    def test_install_force(self, tmp_path: Path) -> None:
        runner = CliRunner()
        runner.invoke(cli_main, ["install-pack", "database", str(tmp_path)])
        result = runner.invoke(
            cli_main, ["install-pack", "database", str(tmp_path), "--force"]
        )
        assert result.exit_code == 0
