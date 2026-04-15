"""Tests for the schema-driven test generation module (Session 19)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from mcptest.cli.main import main
from mcptest.fixtures.models import ErrorSpec, Fixture, Response, ServerSpec, ToolSpec
from mcptest.generate import TestGenerator as _TestGenerator, generate_suite
from mcptest.generate.values import (
    EdgeCaseInput,
    generate_edge_cases,
    generate_from_match,
    generate_missing_required,
    generate_type_error,
    generate_valid,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _tool(
    name: str,
    properties: dict | None = None,
    required: list | None = None,
    responses: list[Response] | None = None,
) -> ToolSpec:
    schema: dict = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    if responses is None:
        responses = [Response(default=True, return_value={"ok": True})]
    return ToolSpec(name=name, input_schema=schema, responses=responses)


def _fixture(*tools: ToolSpec, errors: list[ErrorSpec] | None = None) -> Fixture:
    return Fixture(
        server=ServerSpec(name="test-server"),
        tools=list(tools),
        errors=errors or [],
    )


def _str_prop():
    return {"type": "string"}


def _int_prop(**kw):
    return {"type": "integer", **kw}


def _num_prop(**kw):
    return {"type": "number", **kw}


def _bool_prop():
    return {"type": "boolean"}


def _arr_prop(items=None):
    d = {"type": "array"}
    if items:
        d["items"] = items
    return d


def _obj_prop(properties=None, required=None):
    d: dict = {"type": "object", "properties": properties or {}}
    if required:
        d["required"] = required
    return d


# ---------------------------------------------------------------------------
# Tests: generate_valid
# ---------------------------------------------------------------------------


class TestGenerateValid:
    def test_string_field(self):
        schema = {"type": "object", "properties": {"name": _str_prop()}, "required": ["name"]}
        result = generate_valid(schema)
        assert isinstance(result["name"], str)
        assert len(result["name"]) > 0

    def test_integer_field(self):
        schema = {"type": "object", "properties": {"count": _int_prop()}, "required": ["count"]}
        result = generate_valid(schema)
        assert isinstance(result["count"], int)
        assert result["count"] >= 1

    def test_number_field(self):
        schema = {"type": "object", "properties": {"score": _num_prop()}, "required": ["score"]}
        result = generate_valid(schema)
        assert isinstance(result["score"], float)
        assert result["score"] >= 1.0

    def test_boolean_field(self):
        schema = {"type": "object", "properties": {"flag": _bool_prop()}, "required": ["flag"]}
        result = generate_valid(schema)
        assert isinstance(result["flag"], bool)

    def test_array_field(self):
        schema = {"type": "object", "properties": {"items": _arr_prop()}, "required": ["items"]}
        result = generate_valid(schema)
        assert isinstance(result["items"], list)
        assert len(result["items"]) == 1

    def test_object_field(self):
        schema = {
            "type": "object",
            "properties": {
                "meta": _obj_prop({"key": _str_prop()}, required=["key"])
            },
            "required": ["meta"],
        }
        result = generate_valid(schema)
        assert isinstance(result["meta"], dict)
        assert "key" in result["meta"]

    def test_null_field(self):
        schema = {"type": "object", "properties": {"nothing": {"type": "null"}}, "required": ["nothing"]}
        result = generate_valid(schema)
        assert result["nothing"] is None

    def test_enum_field(self):
        schema = {
            "type": "object",
            "properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}},
            "required": ["color"],
        }
        result = generate_valid(schema)
        assert result["color"] == "red"

    def test_default_used(self):
        schema = {
            "type": "object",
            "properties": {"level": {"type": "integer", "default": 42}},
            "required": ["level"],
        }
        result = generate_valid(schema)
        assert result["level"] == 42

    def test_min_length_satisfied(self):
        schema = {
            "type": "object",
            "properties": {"slug": {"type": "string", "minLength": 20}},
            "required": ["slug"],
        }
        result = generate_valid(schema)
        assert len(result["slug"]) >= 20

    def test_max_length_satisfied(self):
        schema = {
            "type": "object",
            "properties": {"code": {"type": "string", "maxLength": 3}},
            "required": ["code"],
        }
        result = generate_valid(schema)
        assert len(result["code"]) <= 3

    def test_only_required_fields_included(self):
        schema = {
            "type": "object",
            "properties": {
                "req": _str_prop(),
                "opt": _str_prop(),
            },
            "required": ["req"],
        }
        result = generate_valid(schema)
        assert "req" in result
        assert "opt" not in result

    def test_no_required_includes_all_properties(self):
        schema = {
            "type": "object",
            "properties": {"a": _str_prop(), "b": _str_prop()},
        }
        result = generate_valid(schema)
        assert "a" in result
        assert "b" in result

    def test_empty_schema_returns_empty_dict(self):
        result = generate_valid({})
        assert result == {}

    def test_integer_minimum_respected(self):
        schema = {
            "type": "object",
            "properties": {"port": {"type": "integer", "minimum": 1024}},
            "required": ["port"],
        }
        result = generate_valid(schema)
        assert result["port"] >= 1024

    def test_number_minimum_respected(self):
        schema = {
            "type": "object",
            "properties": {"ratio": {"type": "number", "minimum": 2.5}},
            "required": ["ratio"],
        }
        result = generate_valid(schema)
        assert result["ratio"] >= 2.5

    def test_array_items_schema_followed(self):
        schema = {
            "type": "object",
            "properties": {"ids": {"type": "array", "items": {"type": "integer"}}},
            "required": ["ids"],
        }
        result = generate_valid(schema)
        assert isinstance(result["ids"][0], int)

    def test_unknown_type_returns_string(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "exotic"}},
            "required": ["x"],
        }
        result = generate_valid(schema)
        assert isinstance(result["x"], str)

    def test_required_field_not_in_properties_gets_string(self):
        schema = {
            "type": "object",
            "properties": {},
            "required": ["ghost"],
        }
        result = generate_valid(schema)
        assert "ghost" in result
        assert isinstance(result["ghost"], str)

    def test_nested_object_no_required_includes_all(self):
        schema = {
            "type": "object",
            "properties": {
                "cfg": {
                    "type": "object",
                    "properties": {"a": _str_prop(), "b": _str_prop()},
                }
            },
            "required": ["cfg"],
        }
        result = generate_valid(schema)
        assert "a" in result["cfg"]
        assert "b" in result["cfg"]


# ---------------------------------------------------------------------------
# Tests: generate_type_error
# ---------------------------------------------------------------------------


class TestGenerateTypeError:
    def test_string_field_gets_integer(self):
        schema = {
            "type": "object",
            "properties": {"name": _str_prop()},
            "required": ["name"],
        }
        result = generate_type_error(schema, "name")
        assert isinstance(result["name"], int)

    def test_integer_field_gets_string(self):
        schema = {
            "type": "object",
            "properties": {"count": _int_prop()},
            "required": ["count"],
        }
        result = generate_type_error(schema, "count")
        assert isinstance(result["count"], str)

    def test_boolean_field_gets_string(self):
        schema = {
            "type": "object",
            "properties": {"flag": _bool_prop()},
            "required": ["flag"],
        }
        result = generate_type_error(schema, "flag")
        assert isinstance(result["flag"], str)

    def test_array_field_gets_string(self):
        schema = {
            "type": "object",
            "properties": {"tags": _arr_prop()},
            "required": ["tags"],
        }
        result = generate_type_error(schema, "tags")
        assert isinstance(result["tags"], str)

    def test_other_required_fields_still_valid(self):
        schema = {
            "type": "object",
            "properties": {
                "repo": _str_prop(),
                "count": _int_prop(),
            },
            "required": ["repo", "count"],
        }
        result = generate_type_error(schema, "count")
        # repo is still a valid string
        assert isinstance(result["repo"], str)
        # count is wrong type
        assert isinstance(result["count"], str)

    def test_optional_field_added_with_wrong_type(self):
        schema = {
            "type": "object",
            "properties": {"opt": _str_prop()},
            "required": [],
        }
        result = generate_type_error(schema, "opt")
        assert "opt" in result
        assert isinstance(result["opt"], int)

    def test_number_field_gets_string(self):
        schema = {
            "type": "object",
            "properties": {"price": _num_prop()},
            "required": ["price"],
        }
        result = generate_type_error(schema, "price")
        assert isinstance(result["price"], str)


# ---------------------------------------------------------------------------
# Tests: generate_missing_required
# ---------------------------------------------------------------------------


class TestGenerateMissingRequired:
    def test_field_is_absent(self):
        schema = {
            "type": "object",
            "properties": {"repo": _str_prop(), "title": _str_prop()},
            "required": ["repo", "title"],
        }
        result = generate_missing_required(schema, "title")
        assert "title" not in result

    def test_other_fields_present(self):
        schema = {
            "type": "object",
            "properties": {"repo": _str_prop(), "title": _str_prop()},
            "required": ["repo", "title"],
        }
        result = generate_missing_required(schema, "title")
        assert "repo" in result

    def test_no_error_when_field_already_absent(self):
        schema = {
            "type": "object",
            "properties": {"a": _str_prop()},
            "required": ["a"],
        }
        # Removing a field that doesn't exist in base should not raise.
        result = generate_missing_required(schema, "nonexistent")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Tests: generate_edge_cases
# ---------------------------------------------------------------------------


class TestGenerateEdgeCases:
    def test_string_produces_empty_and_long(self):
        schema = {
            "type": "object",
            "properties": {"title": _str_prop()},
            "required": ["title"],
        }
        cases = generate_edge_cases(schema)
        labels = [c.label for c in cases]
        assert "empty-title" in labels
        assert "long-title" in labels

    def test_integer_produces_zero_and_negative(self):
        schema = {
            "type": "object",
            "properties": {"count": _int_prop()},
            "required": ["count"],
        }
        cases = generate_edge_cases(schema)
        labels = [c.label for c in cases]
        assert "zero-count" in labels
        assert "negative-count" in labels

    def test_number_produces_zero_and_negative(self):
        schema = {
            "type": "object",
            "properties": {"ratio": _num_prop()},
            "required": ["ratio"],
        }
        cases = generate_edge_cases(schema)
        labels = [c.label for c in cases]
        assert "zero-ratio" in labels
        assert "negative-ratio" in labels

    def test_array_produces_empty(self):
        schema = {
            "type": "object",
            "properties": {"tags": _arr_prop()},
            "required": ["tags"],
        }
        cases = generate_edge_cases(schema)
        labels = [c.label for c in cases]
        assert "empty-tags" in labels

    def test_integer_with_minimum_above_zero_skips_zero(self):
        schema = {
            "type": "object",
            "properties": {"port": {"type": "integer", "minimum": 1}},
            "required": ["port"],
        }
        cases = generate_edge_cases(schema)
        labels = [c.label for c in cases]
        assert "zero-port" not in labels

    def test_integer_with_minimum_at_zero_skips_negative(self):
        schema = {
            "type": "object",
            "properties": {"n": {"type": "integer", "minimum": 0}},
            "required": ["n"],
        }
        cases = generate_edge_cases(schema)
        labels = [c.label for c in cases]
        assert "negative-n" not in labels
        assert "zero-n" in labels

    def test_non_required_fields_skipped(self):
        schema = {
            "type": "object",
            "properties": {
                "req": _str_prop(),
                "opt": _str_prop(),
            },
            "required": ["req"],
        }
        cases = generate_edge_cases(schema)
        labels = [c.label for c in cases]
        # Only required field "req" should appear
        assert any("req" in lbl for lbl in labels)
        assert not any("opt" in lbl for lbl in labels)

    def test_returns_namedtuples(self):
        schema = {
            "type": "object",
            "properties": {"x": _str_prop()},
            "required": ["x"],
        }
        cases = generate_edge_cases(schema)
        for c in cases:
            assert isinstance(c, EdgeCaseInput)
            assert isinstance(c.label, str)
            assert isinstance(c.args, dict)

    def test_long_string_respects_max_length(self):
        schema = {
            "type": "object",
            "properties": {"code": {"type": "string", "maxLength": 5}},
            "required": ["code"],
        }
        cases = generate_edge_cases(schema)
        long_case = next(c for c in cases if "long" in c.label)
        assert len(long_case.args["code"]) == 5

    def test_empty_schema_returns_empty_list(self):
        cases = generate_edge_cases({})
        assert cases == []

    def test_schema_with_no_required_uses_all_properties(self):
        schema = {
            "type": "object",
            "properties": {"a": _str_prop()},
        }
        cases = generate_edge_cases(schema)
        # No required fields, falls back to all properties.
        labels = [c.label for c in cases]
        assert any("a" in lbl for lbl in labels)


# ---------------------------------------------------------------------------
# Tests: generate_from_match
# ---------------------------------------------------------------------------


class TestGenerateFromMatch:
    def test_returns_copy_of_match(self):
        match = {"repo": "acme/api", "status": "open"}
        result = generate_from_match(match)
        assert result == match
        assert result is not match  # must be a copy

    def test_empty_match(self):
        assert generate_from_match({}) == {}

    def test_values_preserved(self):
        match = {"n": 42, "flag": True}
        result = generate_from_match(match)
        assert result["n"] == 42
        assert result["flag"] is True


# ---------------------------------------------------------------------------
# Tests: TestGenerator — suite structure
# ---------------------------------------------------------------------------


class TestSuiteGeneration:
    def _github_fixture(self) -> Fixture:
        create_issue = _tool(
            "create_issue",
            properties={"repo": _str_prop(), "title": _str_prop(), "body": _str_prop()},
            required=["repo", "title"],
            responses=[
                Response(match={"repo": "acme/api"}, return_value={"issue_number": 42}),
                Response(default=True, return_value={"issue_number": 1}),
            ],
        )
        list_issues = _tool(
            "list_issues",
            responses=[Response(default=True, return_value={"issues": []})],
        )
        rate_limited = ErrorSpec(name="rate_limited", tool="create_issue", error_code=-32000, message="Rate limit")
        return _fixture(create_issue, list_issues, errors=[rate_limited])

    def test_suite_has_required_top_level_keys(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("test", "python a.py")
        assert "name" in suite
        assert "fixtures" in suite
        assert "agent" in suite
        assert "cases" in suite

    def test_suite_name_set(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("my-suite", "python a.py")
        assert suite["name"] == "my-suite"

    def test_agent_cmd_set(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "python agent.py")
        assert suite["agent"]["command"] == "python agent.py"

    def test_timeout_s_set(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", timeout_s=30.0)
        assert suite["agent"]["timeout_s"] == 30.0

    def test_fixture_paths_embedded(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite(
            "s", "cmd", fixture_paths=["fixtures/gh.yaml"]
        )
        assert "fixtures/gh.yaml" in suite["fixtures"]

    def test_happy_path_case_produced(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["happy"])
        names = [c["name"] for c in suite["cases"]]
        assert "create_issue-happy-path" in names

    def test_happy_path_assertions(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["happy"])
        case = next(c for c in suite["cases"] if c["name"] == "create_issue-happy-path")
        assert {"tool_called": "create_issue"} in case["assertions"]
        assert {"no_errors": True} in case["assertions"]

    def test_match_case_produced_for_non_default_response(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["match"])
        names = [c["name"] for c in suite["cases"]]
        assert any("create_issue-match" in n for n in names)

    def test_match_case_input_contains_match_value(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["match"])
        match_case = next(
            c for c in suite["cases"] if "create_issue-match" in c["name"]
        )
        args = json.loads(match_case["input"])["args"]
        assert args["repo"] == "acme/api"

    def test_no_match_case_for_default_only_tool(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["match"])
        names = [c["name"] for c in suite["cases"]]
        assert not any("list_issues-match" in n for n in names)

    def test_type_error_case_per_required_field(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["type_error"])
        names = [c["name"] for c in suite["cases"]]
        assert "create_issue-type-error-repo" in names
        assert "create_issue-type-error-title" in names

    def test_type_error_assertion_is_error_handled(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["type_error"])
        case = next(c for c in suite["cases"] if c["name"] == "create_issue-type-error-repo")
        assert {"error_handled": True} in case["assertions"]

    def test_missing_required_case_per_field(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["missing"])
        names = [c["name"] for c in suite["cases"]]
        assert "create_issue-missing-repo" in names
        assert "create_issue-missing-title" in names

    def test_missing_required_field_absent_in_input(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["missing"])
        case = next(c for c in suite["cases"] if c["name"] == "create_issue-missing-repo")
        args = json.loads(case["input"])["args"]
        assert "repo" not in args

    def test_error_injection_case_produced(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["error"])
        names = [c["name"] for c in suite["cases"]]
        assert "create_issue-error-rate_limited" in names

    def test_error_injection_case_has_inject_error_key(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["error"])
        case = next(c for c in suite["cases"] if "error-rate_limited" in c["name"])
        assert case["inject_error"] == "rate_limited"

    def test_global_error_applied_to_all_tools(self):
        global_error = ErrorSpec(name="server_down", error_code=-32001, message="Down")
        tool_a = _tool("tool_a")
        tool_b = _tool("tool_b")
        fx = _fixture(tool_a, tool_b, errors=[global_error])
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["error"])
        names = [c["name"] for c in suite["cases"]]
        assert "tool_a-error-server_down" in names
        assert "tool_b-error-server_down" in names

    def test_tool_scoped_error_not_applied_to_other_tools(self):
        scoped_error = ErrorSpec(name="my_err", tool="tool_a", error_code=-32000, message="err")
        tool_a = _tool("tool_a")
        tool_b = _tool("tool_b")
        fx = _fixture(tool_a, tool_b, errors=[scoped_error])
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["error"])
        names = [c["name"] for c in suite["cases"]]
        assert "tool_a-error-my_err" in names
        assert "tool_b-error-my_err" not in names

    def test_edge_cases_produced(self):
        fx = self._github_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["edge"])
        names = [c["name"] for c in suite["cases"]]
        assert any("create_issue-edge-" in n for n in names)


# ---------------------------------------------------------------------------
# Tests: category filtering
# ---------------------------------------------------------------------------


class TestCategoryFiltering:
    def _simple_fixture(self) -> Fixture:
        t = _tool("ping", properties={"msg": _str_prop()}, required=["msg"])
        return _fixture(t)

    def test_single_category_happy(self):
        fx = self._simple_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["happy"])
        names = [c["name"] for c in suite["cases"]]
        assert all("happy" in n for n in names)

    def test_single_category_type_error(self):
        fx = self._simple_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["type_error"])
        names = [c["name"] for c in suite["cases"]]
        assert all("type-error" in n for n in names)

    def test_multiple_categories(self):
        fx = self._simple_fixture()
        suite = _TestGenerator([fx]).generate_suite(
            "s", "cmd", categories=["happy", "missing"]
        )
        names = [c["name"] for c in suite["cases"]]
        assert any("happy" in n for n in names)
        assert any("missing" in n for n in names)
        assert not any("type-error" in n for n in names)
        assert not any("edge" in n for n in names)

    def test_all_categories_by_default(self):
        t = _tool(
            "ping",
            properties={"msg": _str_prop()},
            required=["msg"],
        )
        error = ErrorSpec(name="boom", error_code=-32000, message="Boom")
        fx = _fixture(t, errors=[error])
        suite = _TestGenerator([fx]).generate_suite("s", "cmd")
        names = [c["name"] for c in suite["cases"]]
        assert any("happy" in n for n in names)
        assert any("type-error" in n for n in names)
        assert any("missing" in n for n in names)
        assert any("edge" in n for n in names)
        assert any("error" in n for n in names)

    def test_invalid_category_raises_value_error(self):
        fx = self._simple_fixture()
        with pytest.raises(ValueError, match="unknown categories"):
            _TestGenerator([fx]).generate_suite("s", "cmd", categories=["bogus"])

    def test_empty_categories_list_produces_no_cases(self):
        fx = self._simple_fixture()
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=[])
        assert suite["cases"] == []


# ---------------------------------------------------------------------------
# Tests: tool with no input_schema properties
# ---------------------------------------------------------------------------


class TestEdgeCasesNoSchema:
    def test_tool_with_empty_schema_happy_path(self):
        t = _tool("noop")  # no properties, no required
        fx = _fixture(t)
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["happy"])
        assert len(suite["cases"]) == 1
        assert suite["cases"][0]["name"] == "noop-happy-path"

    def test_tool_with_no_required_no_type_errors(self):
        t = _tool("noop")
        fx = _fixture(t)
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["type_error"])
        assert suite["cases"] == []

    def test_tool_with_no_required_no_missing_cases(self):
        t = _tool("noop")
        fx = _fixture(t)
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["missing"])
        assert suite["cases"] == []

    def test_fixture_with_no_errors_no_error_cases(self):
        t = _tool("ping")
        fx = _fixture(t)  # no errors
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["error"])
        assert suite["cases"] == []

    def test_empty_properties_dict(self):
        t = ToolSpec(
            name="empty",
            input_schema={"type": "object", "properties": {}},
            responses=[Response(default=True, return_value={})],
        )
        fx = _fixture(t)
        suite = _TestGenerator([fx]).generate_suite("s", "cmd", categories=["happy"])
        assert len(suite["cases"]) == 1


# ---------------------------------------------------------------------------
# Tests: generate_suite convenience wrapper
# ---------------------------------------------------------------------------


class TestGenerateSuiteWrapper:
    def test_wrapper_matches_class(self):
        t = _tool("ping", properties={"x": _str_prop()}, required=["x"])
        fx = _fixture(t)
        via_wrapper = generate_suite([fx], "n", "cmd", categories=["happy"])
        via_class = _TestGenerator([fx]).generate_suite("n", "cmd", categories=["happy"])
        assert via_wrapper == via_class

    def test_multiple_fixtures_combined(self):
        t1 = _tool("tool1")
        t2 = _tool("tool2")
        fx1 = _fixture(t1)
        fx2 = _fixture(t2)
        suite = generate_suite([fx1, fx2], "s", "cmd", categories=["happy"])
        names = [c["name"] for c in suite["cases"]]
        assert "tool1-happy-path" in names
        assert "tool2-happy-path" in names


# ---------------------------------------------------------------------------
# Tests: CLI integration
# ---------------------------------------------------------------------------


class TestGenerateCommand:
    def _write_fixture(self, tmp_path: Path) -> Path:
        content = """
server:
  name: mock-server
tools:
  - name: greet
    input_schema:
      type: object
      properties:
        name: {type: string}
        lang: {type: string}
      required: [name]
    responses:
      - match: {lang: "es"}
        return: {msg: "Hola"}
      - default: true
        return: {msg: "Hello"}
errors:
  - name: timeout
    tool: greet
    error_code: -32000
    message: Request timed out
"""
        p = tmp_path / "greet.yaml"
        p.write_text(content)
        return p

    def test_stdout_yaml_is_valid(self, tmp_path: Path):
        fp = self._write_fixture(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["generate", str(fp), "--agent", "python a.py"]
        )
        assert result.exit_code == 0, result.output
        suite = yaml.safe_load(result.output)
        assert isinstance(suite, dict)
        assert "cases" in suite

    def test_output_file_written(self, tmp_path: Path):
        fp = self._write_fixture(tmp_path)
        out = tmp_path / "out.yaml"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(fp), "--agent", "python a.py", "--output", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        suite = yaml.safe_load(out.read_text())
        assert "cases" in suite

    def test_name_flag(self, tmp_path: Path):
        fp = self._write_fixture(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(fp), "--agent", "cmd", "--name", "my-custom-suite"],
        )
        assert result.exit_code == 0
        suite = yaml.safe_load(result.output)
        assert suite["name"] == "my-custom-suite"

    def test_default_name_derived_from_fixture(self, tmp_path: Path):
        fp = self._write_fixture(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(fp), "--agent", "cmd"])
        assert result.exit_code == 0
        suite = yaml.safe_load(result.output)
        # File is named greet.yaml → suite name should be "greet-generated"
        assert "greet" in suite["name"]

    def test_categories_flag(self, tmp_path: Path):
        fp = self._write_fixture(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(fp), "--agent", "cmd", "--categories", "happy"],
        )
        assert result.exit_code == 0
        suite = yaml.safe_load(result.output)
        names = [c["name"] for c in suite["cases"]]
        assert all("happy" in n for n in names)

    def test_missing_agent_flag_exits_nonzero(self, tmp_path: Path):
        fp = self._write_fixture(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(fp)])
        assert result.exit_code != 0

    def test_nonexistent_fixture_exits_nonzero(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(tmp_path / "missing.yaml"), "--agent", "cmd"],
        )
        assert result.exit_code != 0

    def test_no_fixture_paths_exits_nonzero(self):
        runner = CliRunner()
        result = runner.invoke(main, ["generate", "--agent", "cmd"])
        assert result.exit_code != 0

    def test_yaml_output_loadable_as_test_suite(self, tmp_path: Path):
        from mcptest.testspec import load_test_suite

        fp = self._write_fixture(tmp_path)
        runner = CliRunner()
        out = tmp_path / "suite.yaml"
        result = runner.invoke(
            main,
            ["generate", str(fp), "--agent", "python a.py", "--output", str(out)],
        )
        assert result.exit_code == 0
        # The generated YAML must be loadable as a valid TestSuite.
        suite = load_test_suite(out)
        assert len(suite.cases) > 0

    def test_invalid_category_exits_nonzero(self, tmp_path: Path):
        fp = self._write_fixture(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(fp), "--agent", "cmd", "--categories", "bogus"],
        )
        assert result.exit_code != 0

    def test_all_six_categories_in_output(self, tmp_path: Path):
        fp = self._write_fixture(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(fp), "--agent", "cmd"])
        assert result.exit_code == 0
        suite = yaml.safe_load(result.output)
        names = [c["name"] for c in suite["cases"]]
        assert any("happy" in n for n in names)
        assert any("match" in n for n in names)
        assert any("type-error" in n for n in names)
        assert any("missing" in n for n in names)
        assert any("edge" in n for n in names)
        assert any("error" in n for n in names)
