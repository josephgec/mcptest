"""Tests for the GitHub Action integration (`github-comment` + `badge`)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest
import yaml
from click.testing import CliRunner

from mcptest.cli.github import (
    _resolve_pr_number,
    badge_command,
    build_badge,
    format_comment,
    github_comment_command,
    post_comment,
)
from mcptest.cli.main import main as cli_main


_PAYLOAD_OK = {
    "passed": 2,
    "failed": 0,
    "total": 2,
    "cases": [
        {"suite": "s", "case": "a", "passed": True, "assertions": []},
        {"suite": "s", "case": "b", "passed": True, "assertions": []},
    ],
}

_PAYLOAD_MIX = {
    "passed": 1,
    "failed": 1,
    "total": 2,
    "cases": [
        {"suite": "s", "case": "ok", "passed": True, "assertions": []},
        {
            "suite": "s",
            "case": "broken",
            "passed": False,
            "error": "something",
            "assertions": [
                {"name": "tool_called", "passed": False, "message": "missing"},
                {"name": "max_tool_calls", "passed": True, "message": "ok"},
            ],
        },
    ],
}


class TestFormatComment:
    def test_all_passed(self) -> None:
        body = format_comment(_PAYLOAD_OK)
        assert "✅" in body
        assert "2 passed" in body
        assert "Failures" not in body

    def test_mixed(self) -> None:
        body = format_comment(_PAYLOAD_MIX)
        assert "❌" in body
        assert "Failures" in body
        assert "s::broken" in body
        assert "tool_called" in body
        assert "something" in body

    def test_empty(self) -> None:
        body = format_comment({})
        assert "mcptest" in body
        assert "0 passed" in body

    def test_failing_case_without_top_level_error(self) -> None:
        body = format_comment(
            {
                "passed": 0,
                "failed": 1,
                "total": 1,
                "cases": [
                    {
                        "suite": "s",
                        "case": "c",
                        "passed": False,
                        "assertions": [
                            {"name": "tool_called", "passed": False, "message": "x"}
                        ],
                    }
                ],
            }
        )
        assert "error:" not in body  # no top-level error line
        assert "tool_called" in body

    # --- Metrics section ---

    def test_metrics_section_present_when_summary_given(self) -> None:
        payload = dict(_PAYLOAD_OK)
        payload["metric_summary"] = {"tool_efficiency": 0.9, "redundancy": 0.75}
        body = format_comment(payload)
        assert "### Metrics" in body
        assert "tool_efficiency" in body
        assert "redundancy" in body

    def test_metrics_section_absent_when_no_summary(self) -> None:
        body = format_comment(_PAYLOAD_OK)
        assert "### Metrics" not in body

    def test_metrics_green_indicator_for_high_score(self) -> None:
        payload = dict(_PAYLOAD_OK)
        payload["metric_summary"] = {"tool_efficiency": 0.95}
        body = format_comment(payload)
        assert "🟢" in body

    def test_metrics_yellow_indicator_for_medium_score(self) -> None:
        payload = dict(_PAYLOAD_OK)
        payload["metric_summary"] = {"tool_efficiency": 0.6}
        body = format_comment(payload)
        assert "🟡" in body

    def test_metrics_red_indicator_for_low_score(self) -> None:
        payload = dict(_PAYLOAD_OK)
        payload["metric_summary"] = {"tool_efficiency": 0.3}
        body = format_comment(payload)
        assert "🔴" in body

    def test_metrics_score_formatted_to_3dp(self) -> None:
        payload = dict(_PAYLOAD_OK)
        payload["metric_summary"] = {"tool_efficiency": 0.12345}
        body = format_comment(payload)
        assert "0.123" in body

    def test_metrics_sorted_alphabetically(self) -> None:
        payload = dict(_PAYLOAD_OK)
        payload["metric_summary"] = {"z_metric": 0.9, "a_metric": 0.8}
        body = format_comment(payload)
        a_pos = body.index("a_metric")
        z_pos = body.index("z_metric")
        assert a_pos < z_pos

    # --- Comparison / regression section ---

    def test_comparison_section_with_regression(self) -> None:
        payload = dict(_PAYLOAD_OK)
        payload["comparison"] = {
            "deltas": [
                {
                    "name": "tool_efficiency",
                    "base_score": 0.9,
                    "head_score": 0.5,
                    "delta": -0.4,
                    "regressed": True,
                }
            ]
        }
        body = format_comment(payload)
        assert "Metric Regressions" in body
        assert "tool_efficiency" in body
        assert "⚠️" in body
        assert "0.900" in body
        assert "0.500" in body

    def test_comparison_section_absent_when_no_regressions(self) -> None:
        payload = dict(_PAYLOAD_OK)
        payload["comparison"] = {
            "deltas": [
                {
                    "name": "tool_efficiency",
                    "base_score": 0.8,
                    "head_score": 0.9,
                    "delta": 0.1,
                    "regressed": False,
                }
            ]
        }
        body = format_comment(payload)
        assert "Metric Regressions" not in body

    def test_comparison_section_absent_when_no_comparison_key(self) -> None:
        body = format_comment(_PAYLOAD_OK)
        assert "Metric Regressions" not in body

    def test_comparison_shows_delta_value(self) -> None:
        payload = dict(_PAYLOAD_OK)
        payload["comparison"] = {
            "deltas": [
                {
                    "name": "redundancy",
                    "base_score": 1.0,
                    "head_score": 0.6,
                    "delta": -0.4,
                    "regressed": True,
                }
            ]
        }
        body = format_comment(payload)
        assert "-0.400" in body

    def test_metrics_and_comparison_coexist(self) -> None:
        payload = dict(_PAYLOAD_OK)
        payload["metric_summary"] = {"tool_efficiency": 0.85}
        payload["comparison"] = {
            "deltas": [
                {
                    "name": "redundancy",
                    "base_score": 1.0,
                    "head_score": 0.5,
                    "delta": -0.5,
                    "regressed": True,
                }
            ]
        }
        body = format_comment(payload)
        assert "### Metrics" in body
        assert "### Metric Regressions" in body


class TestBuildBadge:
    def test_all_passing(self) -> None:
        b = build_badge(_PAYLOAD_OK)
        assert b["color"] == "brightgreen"
        assert b["label"] == "mcptest"
        assert "2 passing" in b["message"]

    def test_mixed(self) -> None:
        b = build_badge(_PAYLOAD_MIX)
        assert b["color"] == "red"
        assert "1/2" in b["message"]

    def test_no_tests(self) -> None:
        b = build_badge({"passed": 0, "failed": 0, "total": 0})
        assert b["color"] == "lightgrey"
        assert b["message"] == "no tests"


class TestResolvePRNumber:
    def test_pull_request_event(self, tmp_path: Path) -> None:
        p = tmp_path / "event.json"
        p.write_text(json.dumps({"pull_request": {"number": 42}}))
        assert _resolve_pr_number(str(p)) == 42

    def test_issue_comment_event(self, tmp_path: Path) -> None:
        p = tmp_path / "event.json"
        p.write_text(json.dumps({"issue": {"number": 7, "pull_request": {}}}))
        assert _resolve_pr_number(str(p)) == 7

    def test_bare_number(self, tmp_path: Path) -> None:
        p = tmp_path / "event.json"
        p.write_text(json.dumps({"number": 99}))
        assert _resolve_pr_number(str(p)) == 99

    def test_none_input(self) -> None:
        assert _resolve_pr_number(None) is None

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _resolve_pr_number(str(tmp_path / "nope.json")) is None

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("[unclosed")
        assert _resolve_pr_number(str(p)) is None

    def test_no_pr_info(self, tmp_path: Path) -> None:
        p = tmp_path / "e.json"
        p.write_text(json.dumps({"action": "push"}))
        assert _resolve_pr_number(str(p)) is None

    def test_zero_number_normalized(self, tmp_path: Path) -> None:
        p = tmp_path / "e.json"
        p.write_text(json.dumps({"pull_request": {"number": 0}}))
        assert _resolve_pr_number(str(p)) is None


class TestPostComment:
    def test_success(self) -> None:
        captured: dict[str, Any] = {}

        def fake_opener(req: Any) -> Any:
            captured["url"] = req.full_url
            captured["headers"] = dict(req.headers)
            captured["body"] = req.data

            class FakeResp:
                status = 201

                def getcode(self) -> int:
                    return 201

            return FakeResp()

        status = post_comment(
            "owner/repo", 7, "hi", "ghp_xxx", opener=fake_opener
        )
        assert status == 201
        assert captured["url"].endswith("/owner/repo/issues/7/comments")
        assert "Bearer ghp_xxx" in captured["headers"]["Authorization"]
        body = json.loads(captured["body"].decode("utf-8"))
        assert body == {"body": "hi"}

    def test_http_error_returns_status(self) -> None:
        def fake_opener(req: Any) -> Any:
            raise HTTPError(
                url="x", code=403, msg="forbidden", hdrs=None, fp=io.BytesIO(b"")
            )

        status = post_comment(
            "o/r", 1, "body", "token", opener=fake_opener
        )
        assert status == 403


class TestGithubCommentCommand:
    def test_dry_run_from_stdin(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_main,
            ["github-comment", "--dry-run"],
            input=json.dumps(_PAYLOAD_OK),
        )
        assert result.exit_code == 0
        assert "mcptest" in result.output

    def test_dry_run_from_file(self, tmp_path: Path) -> None:
        p = tmp_path / "r.json"
        p.write_text(json.dumps(_PAYLOAD_MIX))
        runner = CliRunner()
        result = runner.invoke(
            cli_main, ["github-comment", "--input", str(p), "--dry-run"]
        )
        assert result.exit_code == 0
        assert "Failures" in result.output

    def test_empty_stdin_rejected(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_main, ["github-comment", "--dry-run"], input=""
        )
        assert result.exit_code != 0
        assert "expected JSON" in result.output

    def test_invalid_json_rejected(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_main, ["github-comment", "--dry-run"], input="[unclosed"
        )
        assert result.exit_code != 0
        assert "invalid JSON" in result.output

    def test_missing_env_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_EVENT_PATH"):
            monkeypatch.delenv(var, raising=False)
        runner = CliRunner()
        result = runner.invoke(
            cli_main,
            ["github-comment"],
            input=json.dumps(_PAYLOAD_OK),
        )
        assert result.exit_code != 0
        assert "missing GitHub context" in result.output

    def test_full_post_with_explicit_flags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def fake_opener(req: Any) -> Any:
            captured["url"] = req.full_url

            class R:
                status = 201

                def getcode(self) -> int:
                    return 201

            return R()

        monkeypatch.setattr(
            "mcptest.cli.github._urllib_request.urlopen", fake_opener
        )

        runner = CliRunner()
        result = runner.invoke(
            cli_main,
            [
                "github-comment",
                "--repo",
                "me/here",
                "--pr",
                "1",
                "--token",
                "tok",
            ],
            input=json.dumps(_PAYLOAD_OK),
        )
        assert result.exit_code == 0
        assert "posted comment" in result.output
        assert "me/here/issues/1" in captured["url"]

    def test_http_error_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def bad_opener(req: Any) -> Any:
            raise HTTPError(
                url="x", code=401, msg="nope", hdrs=None, fp=io.BytesIO(b"")
            )

        monkeypatch.setattr(
            "mcptest.cli.github._urllib_request.urlopen", bad_opener
        )
        runner = CliRunner()
        result = runner.invoke(
            cli_main,
            [
                "github-comment",
                "--repo",
                "me/here",
                "--pr",
                "1",
                "--token",
                "tok",
            ],
            input=json.dumps(_PAYLOAD_OK),
        )
        assert result.exit_code != 0
        assert "401" in result.output


class TestBadgeCommand:
    def test_stdout(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_main, ["badge"], input=json.dumps(_PAYLOAD_OK)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["schemaVersion"] == 1
        assert data["color"] == "brightgreen"

    def test_output_file(self, tmp_path: Path) -> None:
        out = tmp_path / "badge.json"
        runner = CliRunner()
        result = runner.invoke(
            cli_main,
            ["badge", "--output", str(out)],
            input=json.dumps(_PAYLOAD_MIX),
        )
        assert result.exit_code == 0
        data = json.loads(out.read_text())
        assert data["color"] == "red"

    def test_from_file(self, tmp_path: Path) -> None:
        p = tmp_path / "r.json"
        p.write_text(json.dumps(_PAYLOAD_OK))
        runner = CliRunner()
        result = runner.invoke(cli_main, ["badge", "--input", str(p)])
        assert result.exit_code == 0


class TestActionYaml:
    """Static checks on the shipped composite action manifest."""

    def test_action_yaml_parses(self) -> None:
        p = Path(__file__).resolve().parent.parent / "action.yml"
        assert p.exists()
        data = yaml.safe_load(p.read_text())
        assert data["name"] == "mcptest"
        assert data["runs"]["using"] == "composite"
        assert isinstance(data["runs"]["steps"], list)
        step_names = {s.get("name", "") for s in data["runs"]["steps"]}
        assert "Set up Python" in step_names
        assert "Install mcptest" in step_names
        assert "Run tests" in step_names

    def test_action_declares_expected_inputs(self) -> None:
        p = Path(__file__).resolve().parent.parent / "action.yml"
        data = yaml.safe_load(p.read_text())
        inputs = data["inputs"]
        for key in (
            "test_path",
            "fail_on_regression",
            "baseline_path",
            "python_version",
            "post_pr_comment",
            "mcptest_version",
        ):
            assert key in inputs
