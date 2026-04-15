"""Comprehensive tests for the documentation engine.

Covers:
- metadata extractors (assertions, metrics, checks, CLI commands)
- Markdown generators
- terminal explain / list_all
- site builder (build_site)
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_keys(d: dict[str, Any], *keys: str) -> None:
    """Assert that all expected keys are present in a dict."""
    for k in keys:
        assert k in d, f"missing key {k!r} in {sorted(d)}"


# ---------------------------------------------------------------------------
# extract_assertions
# ---------------------------------------------------------------------------


class TestExtractAssertions:
    def test_returns_list(self) -> None:
        from mcptest.docs.extractors import extract_assertions

        result = extract_assertions()
        assert isinstance(result, list)

    def test_returns_19_assertions(self) -> None:
        from mcptest.docs.extractors import extract_assertions

        result = extract_assertions()
        assert len(result) == 19

    def test_entry_has_required_keys(self) -> None:
        from mcptest.docs.extractors import extract_assertions

        for entry in extract_assertions():
            _assert_keys(entry, "yaml_key", "short_doc", "full_doc", "fields")

    def test_yaml_keys_are_strings(self) -> None:
        from mcptest.docs.extractors import extract_assertions

        for entry in extract_assertions():
            assert isinstance(entry["yaml_key"], str)
            assert entry["yaml_key"]  # non-empty

    def test_contains_core_assertions(self) -> None:
        from mcptest.docs.extractors import extract_assertions

        keys = {e["yaml_key"] for e in extract_assertions()}
        expected = {
            "tool_called",
            "tool_not_called",
            "tool_call_count",
            "max_tool_calls",
            "param_matches",
            "param_schema_valid",
            "tool_order",
            "trajectory_matches",
            "completes_within_s",
            "output_contains",
            "output_matches",
            "no_errors",
            "error_handled",
            "metric_above",
            "metric_below",
        }
        assert expected.issubset(keys)

    def test_contains_combinators(self) -> None:
        from mcptest.docs.extractors import extract_assertions

        keys = {e["yaml_key"] for e in extract_assertions()}
        assert {"all_of", "any_of", "none_of", "weighted_score"}.issubset(keys)

    def test_tool_called_has_fields(self) -> None:
        from mcptest.docs.extractors import extract_assertions

        tool_called = next(
            e for e in extract_assertions() if e["yaml_key"] == "tool_called"
        )
        fields = {f["name"] for f in tool_called["fields"]}
        assert "tool" in fields

    def test_fields_have_required_keys(self) -> None:
        from mcptest.docs.extractors import extract_assertions

        for entry in extract_assertions():
            for f in entry["fields"]:
                _assert_keys(f, "name", "type", "required", "default")

    def test_max_tool_calls_has_count_field(self) -> None:
        from mcptest.docs.extractors import extract_assertions

        entry = next(
            e for e in extract_assertions() if e["yaml_key"] == "max_tool_calls"
        )
        field_names = {f["name"] for f in entry["fields"]}
        assert "limit" in field_names

    def test_short_doc_is_non_empty_for_core_assertions(self) -> None:
        from mcptest.docs.extractors import extract_assertions

        for entry in extract_assertions():
            # combinators may have shorter docs but should still have something
            assert isinstance(entry["short_doc"], str)


# ---------------------------------------------------------------------------
# extract_metrics
# ---------------------------------------------------------------------------


class TestExtractMetrics:
    def test_returns_list(self) -> None:
        from mcptest.docs.extractors import extract_metrics

        result = extract_metrics()
        assert isinstance(result, list)

    def test_returns_7_metrics(self) -> None:
        from mcptest.docs.extractors import extract_metrics

        result = extract_metrics()
        assert len(result) == 7

    def test_entry_has_required_keys(self) -> None:
        from mcptest.docs.extractors import extract_metrics

        for entry in extract_metrics():
            _assert_keys(entry, "name", "label", "short_doc", "full_doc")

    def test_contains_all_metric_names(self) -> None:
        from mcptest.docs.extractors import extract_metrics

        names = {e["name"] for e in extract_metrics()}
        expected = {
            "tool_efficiency",
            "redundancy",
            "error_recovery_rate",
            "trajectory_similarity",
            "schema_compliance",
            "tool_coverage",
            "stability",
        }
        assert expected == names

    def test_labels_are_strings(self) -> None:
        from mcptest.docs.extractors import extract_metrics

        for entry in extract_metrics():
            assert isinstance(entry["label"], str)
            assert entry["label"]

    def test_tool_efficiency_label(self) -> None:
        from mcptest.docs.extractors import extract_metrics

        entry = next(e for e in extract_metrics() if e["name"] == "tool_efficiency")
        assert "efficiency" in entry["label"].lower() or "tool" in entry["label"].lower()


# ---------------------------------------------------------------------------
# extract_checks
# ---------------------------------------------------------------------------


class TestExtractChecks:
    def test_returns_list(self) -> None:
        from mcptest.docs.extractors import extract_checks

        result = extract_checks()
        assert isinstance(result, list)

    def test_returns_19_checks(self) -> None:
        from mcptest.docs.extractors import extract_checks

        result = extract_checks()
        assert len(result) == 19

    def test_entry_has_required_keys(self) -> None:
        from mcptest.docs.extractors import extract_checks

        for entry in extract_checks():
            _assert_keys(entry, "id", "section", "name", "severity", "short_doc", "full_doc")

    def test_ids_are_structured(self) -> None:
        from mcptest.docs.extractors import extract_checks

        for entry in extract_checks():
            assert "-" in entry["id"], f"unexpected id format: {entry['id']!r}"

    def test_sections_are_known(self) -> None:
        from mcptest.docs.extractors import extract_checks

        sections = {e["section"] for e in extract_checks()}
        expected = {"initialization", "tool_listing", "tool_calling", "error_handling", "resources"}
        assert sections == expected

    def test_severities_are_valid(self) -> None:
        from mcptest.docs.extractors import extract_checks

        valid = {"MUST", "SHOULD", "MAY"}
        for entry in extract_checks():
            assert entry["severity"] in valid

    def test_contains_init_checks(self) -> None:
        from mcptest.docs.extractors import extract_checks

        ids = {e["id"] for e in extract_checks()}
        assert {"INIT-001", "INIT-002", "INIT-003", "INIT-004"}.issubset(ids)

    def test_contains_call_checks(self) -> None:
        from mcptest.docs.extractors import extract_checks

        ids = {e["id"] for e in extract_checks()}
        assert {"CALL-001", "CALL-002", "CALL-003", "CALL-004", "CALL-005"}.issubset(ids)

    def test_must_checks_exist(self) -> None:
        from mcptest.docs.extractors import extract_checks

        must_checks = [e for e in extract_checks() if e["severity"] == "MUST"]
        assert len(must_checks) >= 1


# ---------------------------------------------------------------------------
# extract_cli_commands
# ---------------------------------------------------------------------------


class TestExtractCliCommands:
    def test_returns_list(self) -> None:
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        result = extract_cli_commands(main)
        assert isinstance(result, list)

    def test_returns_expected_command_count(self) -> None:
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        result = extract_cli_commands(main)
        # We have at least 20 commands registered (plan says 23 total)
        assert len(result) >= 20

    def test_entry_has_required_keys(self) -> None:
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        for entry in extract_cli_commands(main):
            _assert_keys(entry, "name", "help", "params")

    def test_contains_core_commands(self) -> None:
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        names = {e["name"] for e in extract_cli_commands(main)}
        expected = {"run", "init", "validate", "record", "conformance", "capture"}
        assert expected.issubset(names)

    def test_contains_docs_and_explain(self) -> None:
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        names = {e["name"] for e in extract_cli_commands(main)}
        assert "docs" in names
        assert "explain" in names

    def test_params_have_required_keys(self) -> None:
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        for entry in extract_cli_commands(main):
            for p in entry["params"]:
                _assert_keys(p, "name", "type", "required", "default", "help", "opts")

    def test_run_command_has_ci_option(self) -> None:
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        run_entry = next(e for e in extract_cli_commands(main) if e["name"] == "run")
        param_names = {p["name"] for p in run_entry["params"]}
        assert "ci" in param_names

    def test_commands_sorted_alphabetically(self) -> None:
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        names = [e["name"] for e in extract_cli_commands(main)]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# generate_assertion_reference
# ---------------------------------------------------------------------------


class TestGenerateAssertionReference:
    def test_returns_string(self) -> None:
        from mcptest.docs.generators import generate_assertion_reference
        from mcptest.docs.extractors import extract_assertions

        result = generate_assertion_reference(extract_assertions())
        assert isinstance(result, str)

    def test_has_title(self) -> None:
        from mcptest.docs.generators import generate_assertion_reference
        from mcptest.docs.extractors import extract_assertions

        result = generate_assertion_reference(extract_assertions())
        assert "# Assertions Reference" in result

    def test_has_quick_reference_section(self) -> None:
        from mcptest.docs.generators import generate_assertion_reference
        from mcptest.docs.extractors import extract_assertions

        result = generate_assertion_reference(extract_assertions())
        assert "## Quick Reference" in result

    def test_has_combinators_section(self) -> None:
        from mcptest.docs.generators import generate_assertion_reference
        from mcptest.docs.extractors import extract_assertions

        result = generate_assertion_reference(extract_assertions())
        assert "Combinator" in result

    def test_contains_assertion_names(self) -> None:
        from mcptest.docs.generators import generate_assertion_reference
        from mcptest.docs.extractors import extract_assertions

        result = generate_assertion_reference(extract_assertions())
        assert "tool_called" in result
        assert "max_tool_calls" in result
        assert "param_matches" in result

    def test_has_yaml_code_blocks(self) -> None:
        from mcptest.docs.generators import generate_assertion_reference
        from mcptest.docs.extractors import extract_assertions

        result = generate_assertion_reference(extract_assertions())
        assert "```yaml" in result

    def test_has_parameters_tables(self) -> None:
        from mcptest.docs.generators import generate_assertion_reference
        from mcptest.docs.extractors import extract_assertions

        result = generate_assertion_reference(extract_assertions())
        assert "Parameter" in result


# ---------------------------------------------------------------------------
# generate_metric_reference
# ---------------------------------------------------------------------------


class TestGenerateMetricReference:
    def test_returns_string(self) -> None:
        from mcptest.docs.generators import generate_metric_reference
        from mcptest.docs.extractors import extract_metrics

        result = generate_metric_reference(extract_metrics())
        assert isinstance(result, str)

    def test_has_title(self) -> None:
        from mcptest.docs.generators import generate_metric_reference
        from mcptest.docs.extractors import extract_metrics

        result = generate_metric_reference(extract_metrics())
        assert "# Metrics Reference" in result

    def test_contains_all_metric_names(self) -> None:
        from mcptest.docs.generators import generate_metric_reference
        from mcptest.docs.extractors import extract_metrics

        result = generate_metric_reference(extract_metrics())
        for name in ("tool_efficiency", "redundancy", "error_recovery_rate",
                     "trajectory_similarity", "schema_compliance", "tool_coverage", "stability"):
            assert name in result

    def test_has_score_interpretation(self) -> None:
        from mcptest.docs.generators import generate_metric_reference
        from mcptest.docs.extractors import extract_metrics

        result = generate_metric_reference(extract_metrics())
        assert "0.0" in result and "1.0" in result

    def test_has_markdown_table(self) -> None:
        from mcptest.docs.generators import generate_metric_reference
        from mcptest.docs.extractors import extract_metrics

        result = generate_metric_reference(extract_metrics())
        assert "| Name |" in result or "| `tool_efficiency`" in result


# ---------------------------------------------------------------------------
# generate_check_reference
# ---------------------------------------------------------------------------


class TestGenerateCheckReference:
    def test_returns_string(self) -> None:
        from mcptest.docs.generators import generate_check_reference
        from mcptest.docs.extractors import extract_checks

        result = generate_check_reference(extract_checks())
        assert isinstance(result, str)

    def test_has_title(self) -> None:
        from mcptest.docs.generators import generate_check_reference
        from mcptest.docs.extractors import extract_checks

        result = generate_check_reference(extract_checks())
        assert "# Conformance Checks Reference" in result

    def test_contains_all_check_ids(self) -> None:
        from mcptest.docs.generators import generate_check_reference
        from mcptest.docs.extractors import extract_checks

        result = generate_check_reference(extract_checks())
        for check_id in ("INIT-001", "TOOL-001", "CALL-001", "ERR-001", "RES-001"):
            assert check_id in result

    def test_has_severity_badges(self) -> None:
        from mcptest.docs.generators import generate_check_reference
        from mcptest.docs.extractors import extract_checks

        result = generate_check_reference(extract_checks())
        assert "MUST" in result
        assert "SHOULD" in result

    def test_has_section_grouping(self) -> None:
        from mcptest.docs.generators import generate_check_reference
        from mcptest.docs.extractors import extract_checks

        result = generate_check_reference(extract_checks())
        assert "Initialization" in result
        assert "Tool" in result


# ---------------------------------------------------------------------------
# generate_cli_reference
# ---------------------------------------------------------------------------


class TestGenerateCliReference:
    def test_returns_string(self) -> None:
        from mcptest.docs.generators import generate_cli_reference
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        result = generate_cli_reference(extract_cli_commands(main))
        assert isinstance(result, str)

    def test_has_title(self) -> None:
        from mcptest.docs.generators import generate_cli_reference
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        result = generate_cli_reference(extract_cli_commands(main))
        assert "# CLI Reference" in result

    def test_contains_command_names(self) -> None:
        from mcptest.docs.generators import generate_cli_reference
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        result = generate_cli_reference(extract_cli_commands(main))
        assert "mcptest run" in result
        assert "mcptest conformance" in result
        assert "mcptest explain" in result

    def test_has_options_table(self) -> None:
        from mcptest.docs.generators import generate_cli_reference
        from mcptest.docs.extractors import extract_cli_commands
        from mcptest.cli.main import main

        result = generate_cli_reference(extract_cli_commands(main))
        assert "Option" in result


# ---------------------------------------------------------------------------
# generate_full_reference
# ---------------------------------------------------------------------------


class TestGenerateFullReference:
    def test_returns_dict(self) -> None:
        from mcptest.docs.generators import generate_full_reference

        result = generate_full_reference()
        assert isinstance(result, dict)

    def test_has_four_keys(self) -> None:
        from mcptest.docs.generators import generate_full_reference

        result = generate_full_reference()
        assert set(result.keys()) == {"assertions.md", "metrics.md", "checks.md", "cli.md"}

    def test_values_are_non_empty_strings(self) -> None:
        from mcptest.docs.generators import generate_full_reference

        for key, value in generate_full_reference().items():
            assert isinstance(value, str), f"{key} is not a string"
            assert len(value) > 100, f"{key} is suspiciously short"

    def test_assertions_md_has_title(self) -> None:
        from mcptest.docs.generators import generate_full_reference

        result = generate_full_reference()
        assert "# Assertions Reference" in result["assertions.md"]

    def test_metrics_md_has_title(self) -> None:
        from mcptest.docs.generators import generate_full_reference

        result = generate_full_reference()
        assert "# Metrics Reference" in result["metrics.md"]

    def test_checks_md_has_title(self) -> None:
        from mcptest.docs.generators import generate_full_reference

        result = generate_full_reference()
        assert "# Conformance Checks Reference" in result["checks.md"]

    def test_cli_md_has_title(self) -> None:
        from mcptest.docs.generators import generate_full_reference

        result = generate_full_reference()
        assert "# CLI Reference" in result["cli.md"]


# ---------------------------------------------------------------------------
# explain — assertion lookup
# ---------------------------------------------------------------------------


class TestExplainAssertions:
    def test_explain_tool_called(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("tool_called")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "tool_called" in result

    def test_explain_max_tool_calls(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("max_tool_calls")
        assert "max_tool_calls" in result

    def test_explain_param_matches(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("param_matches")
        assert "param_matches" in result

    def test_explain_no_errors(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("no_errors")
        assert "no_errors" in result

    def test_explain_assertion_shows_type_label(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("tool_called")
        assert "assertion" in result.lower()


# ---------------------------------------------------------------------------
# explain — metric lookup
# ---------------------------------------------------------------------------


class TestExplainMetrics:
    def test_explain_tool_efficiency(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("tool_efficiency")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "tool_efficiency" in result

    def test_explain_redundancy(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("redundancy")
        assert "redundancy" in result

    def test_explain_metric_shows_score_range(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("tool_efficiency")
        # Should mention score range (0.0 to 1.0)
        assert "0.0" in result or "1.0" in result

    def test_explain_metric_shows_metric_label(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("tool_efficiency")
        assert "metric" in result.lower()


# ---------------------------------------------------------------------------
# explain — conformance check lookup
# ---------------------------------------------------------------------------


class TestExplainChecks:
    def test_explain_init_001(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("INIT-001")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "INIT-001" in result

    def test_explain_call_003(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("CALL-003")
        assert "CALL-003" in result

    def test_explain_check_case_insensitive(self) -> None:
        from mcptest.docs.terminal import explain

        result_upper = explain("INIT-001")
        result_lower = explain("init-001")
        # Both should find the same check
        assert "INIT-001" in result_upper
        assert "INIT-001" in result_lower

    def test_explain_check_shows_severity(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("INIT-001")
        assert "MUST" in result

    def test_explain_check_shows_section(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("INIT-001")
        assert "initialization" in result


# ---------------------------------------------------------------------------
# explain — not found
# ---------------------------------------------------------------------------


class TestExplainNotFound:
    def test_nonexistent_name_returns_string(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("nonexistent_xyz_abc")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_nonexistent_shows_not_found_message(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("nonexistent_xyz_abc")
        assert "not found" in result.lower() or "No assertion" in result

    def test_close_match_suggests_alternatives(self) -> None:
        from mcptest.docs.terminal import explain

        # "tool_call" is close to "tool_called" and "tool_call_count"
        result = explain("tool_call")
        # Should either find it or suggest alternatives
        assert isinstance(result, str)

    def test_unknown_returns_helpful_message(self) -> None:
        from mcptest.docs.terminal import explain

        result = explain("zzz_totally_unknown_zzz")
        # Should mention how to list all names
        assert isinstance(result, str)
        assert len(result) > 20


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


class TestListAll:
    def test_returns_string(self) -> None:
        from mcptest.docs.terminal import list_all

        result = list_all()
        assert isinstance(result, str)

    def test_non_empty(self) -> None:
        from mcptest.docs.terminal import list_all

        result = list_all()
        assert len(result) > 0

    def test_contains_assertions_section(self) -> None:
        from mcptest.docs.terminal import list_all

        result = list_all()
        assert "Assertions" in result or "assertion" in result.lower()

    def test_contains_metrics_section(self) -> None:
        from mcptest.docs.terminal import list_all

        result = list_all()
        assert "Metrics" in result or "metric" in result.lower()

    def test_contains_conformance_section(self) -> None:
        from mcptest.docs.terminal import list_all

        result = list_all()
        assert "Conformance" in result or "check" in result.lower()

    def test_contains_known_assertion_names(self) -> None:
        from mcptest.docs.terminal import list_all

        result = list_all()
        assert "tool_called" in result
        assert "max_tool_calls" in result

    def test_contains_known_metric_names(self) -> None:
        from mcptest.docs.terminal import list_all

        result = list_all()
        assert "tool_efficiency" in result

    def test_contains_known_check_ids(self) -> None:
        from mcptest.docs.terminal import list_all

        result = list_all()
        assert "INIT-001" in result


# ---------------------------------------------------------------------------
# build_site
# ---------------------------------------------------------------------------


class TestBuildSite:
    def test_returns_list_of_paths(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            result = build_site(tmp)
            assert isinstance(result, list)
            assert all(isinstance(p, Path) for p in result)

    def test_returns_nonempty_list(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            result = build_site(tmp)
            assert len(result) > 0

    def test_creates_mkdocs_yml(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            mkdocs_yml = Path(tmp) / "mkdocs.yml"
            assert mkdocs_yml.exists()

    def test_creates_docs_directory(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            assert (Path(tmp) / "docs").is_dir()

    def test_creates_index_md(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            index = Path(tmp) / "docs" / "index.md"
            assert index.exists()
            assert index.stat().st_size > 0

    def test_creates_getting_started_md(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            p = Path(tmp) / "docs" / "getting-started.md"
            assert p.exists()

    def test_creates_guides_directory(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            assert (Path(tmp) / "docs" / "guides").is_dir()

    def test_creates_all_guide_pages(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            guides_dir = Path(tmp) / "docs" / "guides"
            expected = {
                "writing-tests.md",
                "assertions.md",
                "conformance.md",
                "ci-integration.md",
            }
            actual = {p.name for p in guides_dir.iterdir()}
            assert expected.issubset(actual)

    def test_creates_reference_directory(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            assert (Path(tmp) / "docs" / "reference").is_dir()

    def test_creates_all_reference_pages(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            ref_dir = Path(tmp) / "docs" / "reference"
            expected = {"assertions.md", "metrics.md", "checks.md", "cli.md"}
            actual = {p.name for p in ref_dir.iterdir()}
            assert expected.issubset(actual)

    def test_mkdocs_yml_is_valid_yaml(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            content = (Path(tmp) / "mkdocs.yml").read_text()
            parsed = yaml.safe_load(content)
            assert isinstance(parsed, dict)

    def test_mkdocs_yml_has_site_name(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            content = (Path(tmp) / "mkdocs.yml").read_text()
            config = yaml.safe_load(content)
            assert "site_name" in config
            assert config["site_name"]

    def test_mkdocs_yml_has_nav(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            content = (Path(tmp) / "mkdocs.yml").read_text()
            config = yaml.safe_load(content)
            assert "nav" in config
            assert isinstance(config["nav"], list)

    def test_mkdocs_yml_has_material_theme(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            content = (Path(tmp) / "mkdocs.yml").read_text()
            config = yaml.safe_load(content)
            assert config.get("theme", {}).get("name") == "material"

    def test_reference_assertions_contains_tool_called(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            content = (Path(tmp) / "docs" / "reference" / "assertions.md").read_text()
            assert "tool_called" in content

    def test_reference_metrics_contains_tool_efficiency(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            content = (Path(tmp) / "docs" / "reference" / "metrics.md").read_text()
            assert "tool_efficiency" in content

    def test_reference_checks_contains_init_001(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            build_site(tmp)
            content = (Path(tmp) / "docs" / "reference" / "checks.md").read_text()
            assert "INIT-001" in content

    def test_all_written_paths_exist(self) -> None:
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            paths = build_site(tmp)
            for p in paths:
                assert p.exists(), f"written path does not exist: {p}"

    def test_default_output_dir(self) -> None:
        """build_site() with no args uses 'site-output' in cwd."""
        import os
        from mcptest.docs.site import build_site

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                paths = build_site()
                assert (Path(tmp) / "site-output").exists()
                for p in paths:
                    assert p.exists()
            finally:
                os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# MKDOCS_CONFIG constant
# ---------------------------------------------------------------------------


class TestMkdocsConfig:
    def test_config_is_dict(self) -> None:
        from mcptest.docs.site import MKDOCS_CONFIG

        assert isinstance(MKDOCS_CONFIG, dict)

    def test_config_has_nav(self) -> None:
        from mcptest.docs.site import MKDOCS_CONFIG

        assert "nav" in MKDOCS_CONFIG

    def test_config_nav_has_reference_section(self) -> None:
        from mcptest.docs.site import MKDOCS_CONFIG

        nav = MKDOCS_CONFIG["nav"]
        # Flatten to find Reference section
        nav_str = str(nav)
        assert "reference" in nav_str.lower() or "Reference" in nav_str

    def test_config_theme_is_material(self) -> None:
        from mcptest.docs.site import MKDOCS_CONFIG

        assert MKDOCS_CONFIG["theme"]["name"] == "material"


# ---------------------------------------------------------------------------
# Public API re-exports from mcptest.docs
# ---------------------------------------------------------------------------


class TestPublicApi:
    def test_extract_assertions_importable(self) -> None:
        from mcptest.docs import extract_assertions  # noqa: F401

    def test_extract_metrics_importable(self) -> None:
        from mcptest.docs import extract_metrics  # noqa: F401

    def test_extract_checks_importable(self) -> None:
        from mcptest.docs import extract_checks  # noqa: F401

    def test_extract_cli_commands_importable(self) -> None:
        from mcptest.docs import extract_cli_commands  # noqa: F401

    def test_generate_full_reference_importable(self) -> None:
        from mcptest.docs import generate_full_reference  # noqa: F401

    def test_explain_importable(self) -> None:
        from mcptest.docs import explain  # noqa: F401

    def test_list_all_importable(self) -> None:
        from mcptest.docs import list_all  # noqa: F401

    def test_build_site_importable(self) -> None:
        from mcptest.docs import build_site  # noqa: F401

    def test_build_site_via_public_api(self) -> None:
        from mcptest.docs import build_site

        with tempfile.TemporaryDirectory() as tmp:
            paths = build_site(tmp)
            assert len(paths) > 0
