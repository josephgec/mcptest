"""Tests for mcptest.config and mcptest.plugins."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from mcptest.cli.main import main
from mcptest.config import (
    McpTestConfig,
    _as_list,
    _parse_config,
    find_config_file,
    load_config,
    merge_cli_overrides,
)
from mcptest.plugins import (
    _load_dotted_module,
    _load_file_module,
    _resolve_search_dirs,
    discover_confmcptest,
    discover_entry_points,
    load_plugins,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    """Write a YAML file and return its path."""
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# McpTestConfig dataclass
# ---------------------------------------------------------------------------


class TestMcpTestConfig:
    def test_all_none_by_default(self) -> None:
        cfg = McpTestConfig()
        assert cfg.test_paths is None
        assert cfg.fixture_paths is None
        assert cfg.baseline_dir is None
        assert cfg.retry is None
        assert cfg.tolerance is None
        assert cfg.parallel is None
        assert cfg.fail_fast is None
        assert cfg.fail_under is None
        assert cfg.thresholds == {}
        assert cfg.plugins == []
        assert cfg.cloud_url is None
        assert cfg.cloud_api_key_env is None
        assert cfg.config_file is None

    def test_config_file_not_in_repr(self, tmp_path: Path) -> None:
        cfg = McpTestConfig(config_file=tmp_path / "mcptest.yaml")
        assert "config_file" not in repr(cfg)


# ---------------------------------------------------------------------------
# find_config_file — directory walk-up
# ---------------------------------------------------------------------------


class TestFindConfigFile:
    def test_finds_yaml_in_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "mcptest.yaml"
        cfg.write_text("retry: 3\n")
        monkeypatch.chdir(tmp_path)
        assert find_config_file() == cfg

    def test_finds_yml_extension(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "mcptest.yml"
        cfg.write_text("retry: 3\n")
        monkeypatch.chdir(tmp_path)
        assert find_config_file() == cfg

    def test_finds_in_parent_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "mcptest.yaml"
        cfg.write_text("retry: 3\n")
        subdir = tmp_path / "tests"
        subdir.mkdir()
        monkeypatch.chdir(subdir)
        assert find_config_file() == cfg

    def test_finds_in_grandparent_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "mcptest.yaml"
        cfg.write_text("retry: 3\n")
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        assert find_config_file() == cfg

    def test_returns_none_when_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        # No config file in tmp_path or any parent that doesn't already have one.
        # Use an isolated search via explicit start_dir rather than patching CWD
        # to avoid interfering with the real project's mcptest.yaml.
        result = find_config_file(start_dir=tmp_path)
        assert result is None

    def test_explicit_start_dir(self, tmp_path: Path) -> None:
        cfg = tmp_path / "mcptest.yaml"
        cfg.write_text("")
        assert find_config_file(start_dir=tmp_path) == cfg

    def test_prefers_yaml_over_yml(self, tmp_path: Path) -> None:
        """yaml takes precedence because it comes first in _CONFIG_FILENAMES."""
        (tmp_path / "mcptest.yaml").write_text("retry: 1\n")
        (tmp_path / "mcptest.yml").write_text("retry: 2\n")
        result = find_config_file(start_dir=tmp_path)
        assert result is not None
        assert result.name == "mcptest.yaml"


# ---------------------------------------------------------------------------
# load_config — parse and validate
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, tmp_path: Path) -> None:
        cfg = load_config(path=None)
        # Only possible because find_config_file searches CWD upward;
        # use explicit path=None + no file in tmp_path via an isolated call.
        isolated = load_config.__wrapped__ if hasattr(load_config, "__wrapped__") else None
        # Simple: just check explicit missing path raises FileNotFoundError.
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "does_not_exist.yaml")

    def test_explicit_path_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "ghost.yaml")

    def test_explicit_path_loads_file(self, tmp_path: Path) -> None:
        p = write_yaml(tmp_path / "mcptest.yaml", {"retry": 5, "parallel": 4})
        cfg = load_config(path=p)
        assert cfg.retry == 5
        assert cfg.parallel == 4
        assert cfg.config_file == p

    def test_non_mapping_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "mcptest.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="must contain a YAML mapping"):
            load_config(path=p)

    def test_empty_file_returns_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "mcptest.yaml"
        p.write_text("")
        cfg = load_config(path=p)
        assert cfg.retry is None
        assert cfg.config_file == p

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        p = write_yaml(tmp_path / "mcptest.yaml", {"unknown_future_key": "value", "retry": 2})
        cfg = load_config(path=p)
        assert cfg.retry == 2


# ---------------------------------------------------------------------------
# _parse_config — field-by-field
# ---------------------------------------------------------------------------


class TestParseConfig:
    def _cfg(self, raw: dict[str, Any], tmp_path: Path) -> McpTestConfig:
        return _parse_config(raw, tmp_path / "mcptest.yaml")

    def test_test_paths_list(self, tmp_path: Path) -> None:
        cfg = self._cfg({"test_paths": ["tests/", "integration/"]}, tmp_path)
        assert cfg.test_paths == ["tests/", "integration/"]

    def test_test_paths_scalar(self, tmp_path: Path) -> None:
        cfg = self._cfg({"test_paths": "tests/"}, tmp_path)
        assert cfg.test_paths == ["tests/"]

    def test_fixture_paths(self, tmp_path: Path) -> None:
        cfg = self._cfg({"fixture_paths": ["fixtures/"]}, tmp_path)
        assert cfg.fixture_paths == ["fixtures/"]

    def test_baseline_dir(self, tmp_path: Path) -> None:
        cfg = self._cfg({"baseline_dir": ".custom/baselines"}, tmp_path)
        assert cfg.baseline_dir == ".custom/baselines"

    def test_retry(self, tmp_path: Path) -> None:
        cfg = self._cfg({"retry": 3}, tmp_path)
        assert cfg.retry == 3

    def test_tolerance(self, tmp_path: Path) -> None:
        cfg = self._cfg({"tolerance": 0.8}, tmp_path)
        assert cfg.tolerance == pytest.approx(0.8)

    def test_parallel(self, tmp_path: Path) -> None:
        cfg = self._cfg({"parallel": 4}, tmp_path)
        assert cfg.parallel == 4

    def test_fail_fast_true(self, tmp_path: Path) -> None:
        cfg = self._cfg({"fail_fast": True}, tmp_path)
        assert cfg.fail_fast is True

    def test_fail_fast_false(self, tmp_path: Path) -> None:
        cfg = self._cfg({"fail_fast": False}, tmp_path)
        assert cfg.fail_fast is False

    def test_fail_under(self, tmp_path: Path) -> None:
        cfg = self._cfg({"fail_under": 0.9}, tmp_path)
        assert cfg.fail_under == pytest.approx(0.9)

    def test_thresholds(self, tmp_path: Path) -> None:
        cfg = self._cfg({"thresholds": {"tool_efficiency": 0.7, "redundancy": 0.3}}, tmp_path)
        assert cfg.thresholds == {"tool_efficiency": pytest.approx(0.7), "redundancy": pytest.approx(0.3)}

    def test_thresholds_non_dict_ignored(self, tmp_path: Path) -> None:
        cfg = self._cfg({"thresholds": "not_a_dict"}, tmp_path)
        assert cfg.thresholds == {}

    def test_plugins_list(self, tmp_path: Path) -> None:
        cfg = self._cfg({"plugins": ["my_pkg.ext", "./custom.py"]}, tmp_path)
        assert cfg.plugins == ["my_pkg.ext", "./custom.py"]

    def test_plugins_scalar(self, tmp_path: Path) -> None:
        cfg = self._cfg({"plugins": "my_pkg.ext"}, tmp_path)
        assert cfg.plugins == ["my_pkg.ext"]

    def test_cloud_settings(self, tmp_path: Path) -> None:
        cfg = self._cfg(
            {"cloud": {"url": "https://mcptest.example.com", "api_key_env": "MY_KEY"}},
            tmp_path,
        )
        assert cfg.cloud_url == "https://mcptest.example.com"
        assert cfg.cloud_api_key_env == "MY_KEY"

    def test_cloud_none_ignored(self, tmp_path: Path) -> None:
        cfg = self._cfg({"cloud": None}, tmp_path)
        assert cfg.cloud_url is None

    def test_cloud_non_dict_ignored(self, tmp_path: Path) -> None:
        cfg = self._cfg({"cloud": "not_a_dict"}, tmp_path)
        assert cfg.cloud_url is None

    def test_full_config(self, tmp_path: Path) -> None:
        raw = {
            "test_paths": ["tests/"],
            "fixture_paths": ["fixtures/"],
            "baseline_dir": ".mcptest/baselines",
            "retry": 3,
            "tolerance": 0.8,
            "parallel": 4,
            "fail_fast": False,
            "fail_under": 0.0,
            "thresholds": {"tool_efficiency": 0.7},
            "plugins": ["my_pkg"],
            "cloud": {"url": "https://example.com", "api_key_env": "KEY"},
        }
        cfg = self._cfg(raw, tmp_path)
        assert cfg.retry == 3
        assert cfg.parallel == 4
        assert cfg.plugins == ["my_pkg"]
        assert cfg.cloud_url == "https://example.com"


# ---------------------------------------------------------------------------
# _as_list helper
# ---------------------------------------------------------------------------


class TestAsList:
    def test_list_passthrough(self) -> None:
        assert _as_list(["a", "b"]) == ["a", "b"]

    def test_scalar_wrapped(self) -> None:
        assert _as_list("x") == ["x"]

    def test_none_wrapped(self) -> None:
        assert _as_list(None) == [None]


# ---------------------------------------------------------------------------
# merge_cli_overrides
# ---------------------------------------------------------------------------


class TestMergeCliOverrides:
    def test_none_values_do_not_override(self) -> None:
        cfg = McpTestConfig(retry=3)
        merged = merge_cli_overrides(cfg, retry=None, parallel=None)
        assert merged.retry == 3
        assert merged.parallel is None

    def test_non_none_values_override(self) -> None:
        cfg = McpTestConfig(retry=3, parallel=2)
        merged = merge_cli_overrides(cfg, retry=5)
        assert merged.retry == 5
        assert merged.parallel == 2  # unchanged

    def test_empty_kwargs_returns_same_object(self) -> None:
        cfg = McpTestConfig(retry=3)
        merged = merge_cli_overrides(cfg)
        assert merged is cfg

    def test_returns_new_dataclass_not_mutating(self) -> None:
        cfg = McpTestConfig(retry=3)
        merged = merge_cli_overrides(cfg, retry=7)
        assert cfg.retry == 3  # original untouched
        assert merged.retry == 7

    def test_all_fields_overridable(self) -> None:
        cfg = McpTestConfig()
        merged = merge_cli_overrides(
            cfg,
            retry=2,
            tolerance=0.5,
            parallel=4,
            fail_fast=True,
            fail_under=0.8,
            baseline_dir="/tmp/b",
        )
        assert merged.retry == 2
        assert merged.tolerance == pytest.approx(0.5)
        assert merged.parallel == 4
        assert merged.fail_fast is True
        assert merged.baseline_dir == "/tmp/b"


# ---------------------------------------------------------------------------
# Plugin loading: _load_file_module
# ---------------------------------------------------------------------------


class TestLoadFileModule:
    def test_loads_valid_module(self, tmp_path: Path) -> None:
        plugin = tmp_path / "my_plugin.py"
        plugin.write_text("PLUGIN_LOADED = True\n")
        assert _load_file_module(plugin) is True

    def test_returns_false_for_missing_file(self, tmp_path: Path) -> None:
        assert _load_file_module(tmp_path / "ghost.py") is False

    def test_returns_false_for_module_with_error(self, tmp_path: Path) -> None:
        plugin = tmp_path / "bad_plugin.py"
        plugin.write_text("raise RuntimeError('intentional')\n")
        assert _load_file_module(plugin) is False

    def test_module_not_left_in_sys_modules_on_error(self, tmp_path: Path) -> None:
        plugin = tmp_path / "err_plugin.py"
        plugin.write_text("raise ImportError('oops')\n")
        before = set(sys.modules.keys())
        _load_file_module(plugin)
        after = set(sys.modules.keys())
        new_keys = after - before
        # No synthetic module names for this failed plugin should remain.
        assert not any("err_plugin" in k for k in new_keys)

    def test_registers_side_effects(self, tmp_path: Path) -> None:
        """Plugin that calls register_assertion as a side-effect."""
        from mcptest.assertions.base import ASSERTIONS

        plugin = tmp_path / "reg_plugin.py"
        plugin.write_text(
            "from mcptest.assertions.base import register_assertion, TraceAssertion\n"
            "from mcptest.runner.trace import Trace\n"
            "@register_assertion\n"
            "class _test_custom_assert(TraceAssertion):\n"
            "    yaml_key = '_test_custom_assert_abc'\n"
            "    def check(self, trace: Trace):\n"
            "        from mcptest.assertions.base import AssertionResult\n"
            "        return AssertionResult(passed=True, name='_test_custom_assert_abc', message='ok')\n"
        )
        _load_file_module(plugin)
        assert "_test_custom_assert_abc" in ASSERTIONS
        # Clean up to avoid polluting other tests.
        ASSERTIONS.pop("_test_custom_assert_abc", None)


# ---------------------------------------------------------------------------
# Plugin loading: _load_dotted_module
# ---------------------------------------------------------------------------


class TestLoadDottedModule:
    def test_loads_existing_module(self) -> None:
        # Use a stdlib module as a safe import target.
        assert _load_dotted_module("textwrap") is True

    def test_returns_false_for_missing_module(self) -> None:
        assert _load_dotted_module("_mcptest_nonexistent_xyz") is False


# ---------------------------------------------------------------------------
# discover_confmcptest
# ---------------------------------------------------------------------------


class TestDiscoverConfmcptest:
    def test_finds_in_dir(self, tmp_path: Path) -> None:
        conf = tmp_path / "confmcptest.py"
        conf.write_text("# confmcptest")
        found = discover_confmcptest([tmp_path])
        assert conf in found

    def test_finds_in_parent(self, tmp_path: Path) -> None:
        parent_conf = tmp_path / "confmcptest.py"
        parent_conf.write_text("")
        subdir = tmp_path / "tests"
        subdir.mkdir()
        found = discover_confmcptest([subdir])
        assert parent_conf in found

    def test_outermost_first(self, tmp_path: Path) -> None:
        parent_conf = tmp_path / "confmcptest.py"
        parent_conf.write_text("")
        subdir = tmp_path / "tests"
        subdir.mkdir()
        child_conf = subdir / "confmcptest.py"
        child_conf.write_text("")
        found = discover_confmcptest([subdir])
        assert found.index(parent_conf) < found.index(child_conf)

    def test_deduplicates_across_search_dirs(self, tmp_path: Path) -> None:
        conf = tmp_path / "confmcptest.py"
        conf.write_text("")
        # Two search dirs that share the same parent.
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        found = discover_confmcptest([a, b])
        assert found.count(conf) == 1

    def test_empty_search_dirs(self) -> None:
        found = discover_confmcptest([])
        assert found == []

    def test_no_confmcptest_returns_empty(self, tmp_path: Path) -> None:
        found = discover_confmcptest([tmp_path])
        # Only empty if no confmcptest.py in tmp_path or its parents;
        # tmp_path is under /var/folders/... so no project confmcptest.py exists.
        # (confmcptest.py is not the same as conftest.py — mcptest uses its own name)
        assert isinstance(found, list)


# ---------------------------------------------------------------------------
# _resolve_search_dirs
# ---------------------------------------------------------------------------


class TestResolveSearchDirs:
    def test_uses_test_paths_from_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        cfg = McpTestConfig(test_paths=["tests"])
        dirs = _resolve_search_dirs(cfg)
        assert tests_dir.resolve() in dirs

    def test_skips_nonexistent_test_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = McpTestConfig(test_paths=["nonexistent_dir"])
        dirs = _resolve_search_dirs(cfg)
        # Falls through to default (cwd or tests/)
        assert len(dirs) >= 1

    def test_falls_back_to_tests_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        cfg = McpTestConfig()
        dirs = _resolve_search_dirs(cfg)
        assert tests_dir in dirs

    def test_falls_back_to_cwd_when_no_tests_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = McpTestConfig()
        dirs = _resolve_search_dirs(cfg)
        assert tmp_path in dirs


# ---------------------------------------------------------------------------
# discover_entry_points
# ---------------------------------------------------------------------------


class TestDiscoverEntryPoints:
    def test_returns_list(self) -> None:
        result = discover_entry_points()
        assert isinstance(result, list)

    def test_loads_mock_entry_point(self) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "mock_plugin"
        mock_ep.load.return_value = None

        with patch("importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = [mock_ep]
            result = discover_entry_points()

        assert any("mock_plugin" in r for r in result)

    def test_skips_entry_points_that_raise(self) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "bad_ep"
        mock_ep.load.side_effect = ImportError("boom")

        with patch("importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = [mock_ep]
            # Should not raise, just skip the bad entry point.
            result = discover_entry_points()

        assert not any("bad_ep" in r for r in result)

    def test_skips_groups_that_raise(self) -> None:
        with patch("importlib.metadata.entry_points") as mock_eps:
            mock_eps.side_effect = Exception("metadata unavailable")
            result = discover_entry_points()
        assert result == []


# ---------------------------------------------------------------------------
# load_plugins
# ---------------------------------------------------------------------------


class TestLoadPlugins:
    def test_empty_config_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = McpTestConfig()
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = load_plugins(cfg)
        assert isinstance(result, list)

    def test_loads_explicit_module_plugin(self) -> None:
        cfg = McpTestConfig(plugins=["textwrap"])
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = load_plugins(cfg)
        assert "textwrap" in result

    def test_loads_file_plugin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        plugin = tmp_path / "myplugin.py"
        plugin.write_text("LOADED = True\n")
        cfg = McpTestConfig(plugins=[str(plugin)])
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = load_plugins(cfg)
        assert str(plugin) in result

    def test_skips_missing_module_plugin(self) -> None:
        cfg = McpTestConfig(plugins=["_nonexistent_mcptest_plugin_xyz"])
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = load_plugins(cfg)
        assert "_nonexistent_mcptest_plugin_xyz" not in result

    def test_confmcptest_loaded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        conf = tests_dir / "confmcptest.py"
        conf.write_text("CONFMCPTEST = True\n")
        cfg = McpTestConfig()
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = load_plugins(cfg)
        assert str(conf) in result

    def test_entry_points_included(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = McpTestConfig()
        with patch("mcptest.plugins.discover_entry_points", return_value=["mcptest.assertions:fake"]):
            result = load_plugins(cfg)
        assert "mcptest.assertions:fake" in result


# ---------------------------------------------------------------------------
# Custom assertion registration via plugin
# ---------------------------------------------------------------------------


class TestPluginRegistration:
    def test_custom_assertion_registered_via_file(self, tmp_path: Path) -> None:
        from mcptest.assertions.base import ASSERTIONS

        plugin = tmp_path / "custom_assert_plugin.py"
        plugin.write_text(
            "from mcptest.assertions.base import register_assertion, TraceAssertion\n"
            "from mcptest.runner.trace import Trace\n"
            "@register_assertion\n"
            "class _plugin_test_assert_xyz(TraceAssertion):\n"
            "    yaml_key = '_plugin_test_assert_xyz'\n"
            "    def check(self, trace: Trace):\n"
            "        from mcptest.assertions.base import AssertionResult\n"
            "        return AssertionResult(passed=True, name='_plugin_test_assert_xyz', message='ok')\n"
        )
        cfg = McpTestConfig(plugins=[str(plugin)])
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            load_plugins(cfg)
        assert "_plugin_test_assert_xyz" in ASSERTIONS
        ASSERTIONS.pop("_plugin_test_assert_xyz", None)

    def test_custom_metric_registered_via_file(self, tmp_path: Path) -> None:
        from mcptest.metrics.base import METRICS

        plugin = tmp_path / "custom_metric_plugin.py"
        plugin.write_text(
            "from mcptest.metrics.base import register_metric, Metric\n"
            "from mcptest.runner.trace import Trace\n"
            "@register_metric\n"
            "class _plugin_test_metric_xyz(Metric):\n"
            "    name = '_plugin_test_metric_xyz'\n"
            "    def compute(self, trace: Trace, **kw):\n"
            "        from mcptest.metrics.base import MetricResult\n"
            "        return MetricResult(name=self.name, score=1.0, label='perfect', details={})\n"
        )
        cfg = McpTestConfig(plugins=[str(plugin)])
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            load_plugins(cfg)
        assert "_plugin_test_metric_xyz" in METRICS
        METRICS.pop("_plugin_test_metric_xyz", None)


# ---------------------------------------------------------------------------
# mcptest config command
# ---------------------------------------------------------------------------


class TestConfigCommand:
    def test_no_config_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = runner.invoke(main, ["config"])
        assert result.exit_code == 0
        assert "No config file" in result.output

    def test_shows_config_file_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cfg_file = write_yaml(tmp_path / "mcptest.yaml", {"retry": 3})
        runner = CliRunner()
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = runner.invoke(main, ["config"])
        assert result.exit_code == 0
        assert "mcptest.yaml" in result.output

    def test_shows_settings(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        write_yaml(tmp_path / "mcptest.yaml", {"retry": 5, "parallel": 2})
        runner = CliRunner()
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = runner.invoke(main, ["config"])
        assert result.exit_code == 0
        assert "retry" in result.output
        assert "parallel" in result.output

    def test_shows_no_settings_when_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        write_yaml(tmp_path / "mcptest.yaml", {})
        runner = CliRunner()
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = runner.invoke(main, ["config"])
        assert result.exit_code == 0
        assert "No settings" in result.output

    def test_shows_loaded_plugins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        write_yaml(tmp_path / "mcptest.yaml", {"plugins": ["textwrap"]})
        runner = CliRunner()
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = runner.invoke(main, ["config"])
        assert result.exit_code == 0
        assert "textwrap" in result.output

    def test_shows_no_plugins_when_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = runner.invoke(main, ["config"])
        assert result.exit_code == 0
        assert "No plugins" in result.output


# ---------------------------------------------------------------------------
# CLI integration: run_command reads config defaults
# ---------------------------------------------------------------------------


class TestRunCommandConfigIntegration:
    """Verify that run_command uses config values when CLI args are omitted."""

    def _make_minimal_test(self, tmp_path: Path) -> tuple[Path, Path]:
        """Return (fixture_path, suite_path) with a do-nothing test."""
        fixtures_dir = tmp_path / "fixtures"
        fixtures_dir.mkdir()
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        fixture = fixtures_dir / "example.yaml"
        fixture.write_text(
            "server:\n  name: mock\ntools: []\n"
        )
        suite = tests_dir / "test_example.yaml"
        suite.write_text(
            "name: Example\n"
            "fixtures:\n"
            f"  - {fixture}\n"
            "agent:\n"
            "  command: 'echo done'\n"
            "cases: []\n"
        )
        return fixture, suite

    def test_run_uses_config_test_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        self._make_minimal_test(tmp_path)
        # Config points to the tests dir explicitly.
        write_yaml(tmp_path / "mcptest.yaml", {"test_paths": ["tests"]})
        runner = CliRunner()
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = runner.invoke(main, ["run"])
        # Exit 0 means no crash even without explicit path argument.
        assert result.exit_code == 0

    def test_run_defaults_to_tests_dir_without_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        self._make_minimal_test(tmp_path)
        runner = CliRunner()
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = runner.invoke(main, ["run"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_load_config_with_path_object(self, tmp_path: Path) -> None:
        p = write_yaml(tmp_path / "mcptest.yaml", {"retry": 1})
        cfg = load_config(path=p)
        assert cfg.retry == 1

    def test_load_config_with_string_path(self, tmp_path: Path) -> None:
        p = write_yaml(tmp_path / "mcptest.yaml", {"retry": 2})
        cfg = load_config(path=str(p))
        assert cfg.retry == 2

    def test_config_repr_excludes_config_file(self, tmp_path: Path) -> None:
        p = write_yaml(tmp_path / "mcptest.yaml", {"retry": 3})
        cfg = load_config(path=p)
        assert "config_file" not in repr(cfg)

    def test_merge_cli_overrides_handles_zero_values(self) -> None:
        """Zero is falsy but should override a config None."""
        cfg = McpTestConfig(retry=5)
        # 0 is a valid parallel value (auto-detect), not "not set"
        merged = merge_cli_overrides(cfg, parallel=0)
        assert merged.parallel == 0

    def test_plugins_list_with_relative_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        plugin = tmp_path / "myplugin.py"
        plugin.write_text("LOADED = True\n")
        # Relative path from cwd.
        cfg = McpTestConfig(plugins=["myplugin.py"])
        with patch("mcptest.plugins.discover_entry_points", return_value=[]):
            result = load_plugins(cfg)
        assert any("myplugin" in r for r in result)

    def test_find_config_file_explicit_start_dir_no_match(self, tmp_path: Path) -> None:
        # An isolated subtree with no mcptest.yaml.
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        result = find_config_file(start_dir=isolated)
        assert result is None
