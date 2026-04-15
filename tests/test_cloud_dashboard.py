"""Tests for the cloud dashboard web UI routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcptest.cloud import Settings, create_app


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'dash_test.db'}")
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


def _run_payload(
    trace_id: str = "t1",
    suite: str = "smoke",
    case: str = "one",
    passed: bool = True,
    branch: str | None = None,
    environment: str | None = None,
    metric_scores: dict | None = None,
    duration_s: float = 1.5,
    total_tool_calls: int = 2,
) -> dict:
    return {
        "trace_id": trace_id,
        "suite": suite,
        "case": case,
        "input": f"input for {trace_id}",
        "output": f"output for {trace_id}",
        "exit_code": 0 if passed else 1,
        "duration_s": duration_s,
        "total_tool_calls": total_tool_calls,
        "passed": passed,
        "tool_calls": [
            {"tool": "list_files", "arguments": {"path": "/"}, "result": ["a.txt"]},
            {"tool": "read_file", "arguments": {"path": "a.txt"}, "result": "hello"},
        ],
        "run_metadata": {"branch": branch or "main"},
        "metric_scores": metric_scores or {},
        "branch": branch,
        "environment": environment,
        "git_sha": "abc1234",
    }


def _create_run(client: TestClient, **kwargs) -> dict:
    resp = client.post("/runs", json=_run_payload(**kwargs))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# TestDashboardHome
# ---------------------------------------------------------------------------


class TestDashboardHome:
    def test_empty_state_renders(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text
        assert "Total Runs" in resp.text

    def test_stats_reflect_runs(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="h1", passed=True)
        _create_run(app_client, trace_id="h2", passed=False)
        _create_run(app_client, trace_id="h3", passed=True)

        resp = app_client.get("/dashboard/")
        assert resp.status_code == 200
        # 2 of 3 passed → 66.7% pass rate shown
        assert "Pass Rate" in resp.text
        assert "3" in resp.text  # total count

    def test_recent_runs_table_shown(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="hr1", suite="alpha")
        resp = app_client.get("/dashboard/")
        assert resp.status_code == 200
        assert "alpha" in resp.text
        assert "Recent Runs" in resp.text

    def test_suite_breakdown_visible(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="sb1", suite="beta", passed=True)
        _create_run(app_client, trace_id="sb2", suite="beta", passed=False)
        resp = app_client.get("/dashboard/")
        assert resp.status_code == 200
        assert "Suite Breakdown" in resp.text
        assert "beta" in resp.text

    def test_baseline_count_shown(self, app_client: TestClient) -> None:
        run = _create_run(app_client, trace_id="bl1", suite="mysuite")
        app_client.post(f"/runs/{run['id']}/promote")
        resp = app_client.get("/dashboard/")
        assert resp.status_code == 200
        assert "Baselines" in resp.text

    def test_no_runs_empty_message(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/")
        assert resp.status_code == 200
        assert "No runs yet" in resp.text


# ---------------------------------------------------------------------------
# TestDashboardRuns
# ---------------------------------------------------------------------------


class TestDashboardRuns:
    def test_empty_state(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/runs")
        assert resp.status_code == 200
        assert "Runs" in resp.text
        assert "No runs match" in resp.text

    def test_runs_listed(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="rl1", suite="smoke")
        _create_run(app_client, trace_id="rl2", suite="smoke")
        resp = app_client.get("/dashboard/runs")
        assert resp.status_code == 200
        assert "smoke" in resp.text
        assert "2 runs found" in resp.text

    def test_filter_by_suite(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="fs1", suite="alpha")
        _create_run(app_client, trace_id="fs2", suite="beta")
        resp = app_client.get("/dashboard/runs?suite=alpha")
        assert resp.status_code == 200
        assert "alpha" in resp.text
        assert "1 run found" in resp.text

    def test_filter_by_branch(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="fb1", branch="main")
        _create_run(app_client, trace_id="fb2", branch="dev")
        resp = app_client.get("/dashboard/runs?branch=dev")
        assert resp.status_code == 200
        assert "1 run found" in resp.text

    def test_filter_by_passed_true(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="fp1", passed=True)
        _create_run(app_client, trace_id="fp2", passed=False)
        resp = app_client.get("/dashboard/runs?passed=true")
        assert resp.status_code == 200
        assert "1 run found" in resp.text

    def test_filter_by_passed_false(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="ff1", passed=True)
        _create_run(app_client, trace_id="ff2", passed=False)
        resp = app_client.get("/dashboard/runs?passed=false")
        assert resp.status_code == 200
        assert "1 run found" in resp.text

    def test_pagination_first_page(self, app_client: TestClient) -> None:
        for i in range(30):
            _create_run(app_client, trace_id=f"pg{i}", suite="pg")
        resp = app_client.get("/dashboard/runs?per_page=10&page=1")
        assert resp.status_code == 200
        assert "30 runs found" in resp.text

    def test_pagination_last_page(self, app_client: TestClient) -> None:
        for i in range(5):
            _create_run(app_client, trace_id=f"pgl{i}", suite="pgl")
        resp = app_client.get("/dashboard/runs?per_page=3&page=2")
        assert resp.status_code == 200

    def test_htmx_request_returns_partial(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="htmx1")
        resp = app_client.get("/dashboard/runs", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        # Partial doesn't include the full page nav
        assert "mcptest" not in resp.text.split("<nav")[0] if "<nav" in resp.text else True

    def test_link_to_run_detail_present(self, app_client: TestClient) -> None:
        run = _create_run(app_client, trace_id="lnk1", suite="mysuite")
        resp = app_client.get("/dashboard/runs")
        assert resp.status_code == 200
        assert f"/dashboard/runs/{run['id']}" in resp.text


# ---------------------------------------------------------------------------
# TestDashboardRunDetail
# ---------------------------------------------------------------------------


class TestDashboardRunDetail:
    def test_run_detail_renders(self, app_client: TestClient) -> None:
        run = _create_run(
            app_client,
            trace_id="rd1",
            suite="detail-suite",
            case="detail-case",
            metric_scores={"tool_efficiency": 0.85},
        )
        resp = app_client.get(f"/dashboard/runs/{run['id']}")
        assert resp.status_code == 200
        assert "detail-suite" in resp.text
        assert "detail-case" in resp.text
        assert f"Run #{run['id']}" in resp.text

    def test_metric_scores_displayed(self, app_client: TestClient) -> None:
        run = _create_run(
            app_client,
            trace_id="rd2",
            metric_scores={"tool_efficiency": 0.9, "redundancy": 0.75},
        )
        resp = app_client.get(f"/dashboard/runs/{run['id']}")
        assert resp.status_code == 200
        assert "tool_efficiency" in resp.text
        assert "redundancy" in resp.text

    def test_tool_calls_shown(self, app_client: TestClient) -> None:
        run = _create_run(app_client, trace_id="rd3")
        resp = app_client.get(f"/dashboard/runs/{run['id']}")
        assert resp.status_code == 200
        assert "Tool Calls" in resp.text
        assert "list_files" in resp.text

    def test_pass_badge_shown(self, app_client: TestClient) -> None:
        run = _create_run(app_client, trace_id="rd4", passed=True)
        resp = app_client.get(f"/dashboard/runs/{run['id']}")
        assert resp.status_code == 200
        assert "Passed" in resp.text

    def test_fail_badge_shown(self, app_client: TestClient) -> None:
        run = _create_run(app_client, trace_id="rd5", passed=False)
        resp = app_client.get(f"/dashboard/runs/{run['id']}")
        assert resp.status_code == 200
        assert "Failed" in resp.text

    def test_baseline_badge_when_promoted(self, app_client: TestClient) -> None:
        run = _create_run(app_client, trace_id="rd6", suite="bls")
        app_client.post(f"/runs/{run['id']}/promote")
        resp = app_client.get(f"/dashboard/runs/{run['id']}")
        assert resp.status_code == 200
        assert "Baseline" in resp.text

    def test_git_sha_displayed(self, app_client: TestClient) -> None:
        run = _create_run(app_client, trace_id="rd7")
        resp = app_client.get(f"/dashboard/runs/{run['id']}")
        assert resp.status_code == 200
        assert "abc1234" in resp.text

    def test_404_for_missing_run(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/runs/99999")
        assert resp.status_code == 404
        assert "Not Found" in resp.text

    def test_promote_button_visible_when_not_baseline(
        self, app_client: TestClient
    ) -> None:
        run = _create_run(app_client, trace_id="rd8", suite="promo")
        resp = app_client.get(f"/dashboard/runs/{run['id']}")
        assert resp.status_code == 200
        assert "Promote as Baseline" in resp.text

    def test_demote_button_visible_when_baseline(
        self, app_client: TestClient
    ) -> None:
        run = _create_run(app_client, trace_id="rd9", suite="demote")
        app_client.post(f"/runs/{run['id']}/promote")
        resp = app_client.get(f"/dashboard/runs/{run['id']}")
        assert resp.status_code == 200
        assert "Demote Baseline" in resp.text


# ---------------------------------------------------------------------------
# TestDashboardTrends
# ---------------------------------------------------------------------------


class TestDashboardTrends:
    def test_trends_page_renders(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/trends")
        assert resp.status_code == 200
        assert "Metric Trends" in resp.text

    def test_trends_controls_present(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/trends")
        assert resp.status_code == 200
        assert "metric" in resp.text.lower()

    def test_trends_shows_suites_in_dropdown(
        self, app_client: TestClient
    ) -> None:
        _create_run(app_client, trace_id="tr1", suite="my-suite")
        resp = app_client.get("/dashboard/trends")
        assert resp.status_code == 200
        assert "my-suite" in resp.text

    def test_trends_shows_metrics_in_dropdown(
        self, app_client: TestClient
    ) -> None:
        _create_run(
            app_client,
            trace_id="tr2",
            metric_scores={"custom_metric": 0.9},
        )
        resp = app_client.get("/dashboard/trends")
        assert resp.status_code == 200
        assert "custom_metric" in resp.text


# ---------------------------------------------------------------------------
# TestDashboardTrendsData
# ---------------------------------------------------------------------------


class TestDashboardTrendsData:
    def test_empty_returns_valid_json(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/trends/data")
        assert resp.status_code == 200
        data = resp.json()
        assert "labels" in data
        assert "values" in data
        assert "baseline_flags" in data
        assert data["count"] == 0

    def test_returns_metric_values(self, app_client: TestClient) -> None:
        _create_run(
            app_client,
            trace_id="td1",
            metric_scores={"tool_efficiency": 0.8},
        )
        _create_run(
            app_client,
            trace_id="td2",
            metric_scores={"tool_efficiency": 0.9},
        )
        resp = app_client.get("/dashboard/trends/data?metric=tool_efficiency")
        assert resp.status_code == 200
        data = resp.json()
        assert data["metric"] == "tool_efficiency"
        assert data["count"] == 2
        non_null = [v for v in data["values"] if v is not None]
        assert len(non_null) == 2

    def test_filter_by_suite(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="ts1", suite="s1", metric_scores={"m": 0.7})
        _create_run(app_client, trace_id="ts2", suite="s2", metric_scores={"m": 0.8})
        resp = app_client.get("/dashboard/trends/data?suite=s1&metric=m")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_filter_by_branch(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="tb1", branch="main", metric_scores={"m": 0.5})
        _create_run(app_client, trace_id="tb2", branch="dev", metric_scores={"m": 0.6})
        resp = app_client.get("/dashboard/trends/data?branch=main&metric=m")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_limit_enforced(self, app_client: TestClient) -> None:
        for i in range(20):
            _create_run(app_client, trace_id=f"lim{i}")
        resp = app_client.get("/dashboard/trends/data?limit=10")
        assert resp.status_code == 200
        assert resp.json()["count"] == 10

    def test_baseline_flags_marked(self, app_client: TestClient) -> None:
        run = _create_run(app_client, trace_id="bf1", suite="bfs")
        app_client.post(f"/runs/{run['id']}/promote")
        _create_run(app_client, trace_id="bf2", suite="bfs")
        resp = app_client.get("/dashboard/trends/data?suite=bfs")
        assert resp.status_code == 200
        flags = resp.json()["baseline_flags"]
        assert True in flags

    def test_null_values_for_missing_metric(self, app_client: TestClient) -> None:
        _create_run(app_client, trace_id="nm1", metric_scores={})
        resp = app_client.get("/dashboard/trends/data?metric=nonexistent_metric")
        assert resp.status_code == 200
        data = resp.json()
        assert data["values"][0] is None

    def test_limit_below_minimum_rejected(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/trends/data?limit=1")
        assert resp.status_code == 422

    def test_limit_above_maximum_rejected(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/trends/data?limit=999")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# TestDashboardBaselines
# ---------------------------------------------------------------------------


class TestDashboardBaselines:
    def test_empty_state(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/baselines")
        assert resp.status_code == 200
        assert "Baselines" in resp.text
        assert "No baselines set" in resp.text

    def test_baseline_appears_after_promote(self, app_client: TestClient) -> None:
        run = _create_run(app_client, trace_id="bl1", suite="smoke")
        app_client.post(f"/runs/{run['id']}/promote")
        resp = app_client.get("/dashboard/baselines")
        assert resp.status_code == 200
        assert "smoke" in resp.text
        assert "Active Baselines" in resp.text

    def test_multiple_baselines_different_suites(
        self, app_client: TestClient
    ) -> None:
        r1 = _create_run(app_client, trace_id="mb1", suite="alpha")
        r2 = _create_run(app_client, trace_id="mb2", suite="beta")
        app_client.post(f"/runs/{r1['id']}/promote")
        app_client.post(f"/runs/{r2['id']}/promote")
        resp = app_client.get("/dashboard/baselines")
        assert resp.status_code == 200
        assert "(2)" in resp.text

    def test_metric_scores_displayed_in_baseline(
        self, app_client: TestClient
    ) -> None:
        run = _create_run(
            app_client,
            trace_id="bm1",
            suite="scored",
            metric_scores={"tool_efficiency": 0.88},
        )
        app_client.post(f"/runs/{run['id']}/promote")
        resp = app_client.get("/dashboard/baselines")
        assert resp.status_code == 200
        assert "tool_efficiency" in resp.text

    def test_compare_section_present(self, app_client: TestClient) -> None:
        resp = app_client.get("/dashboard/baselines")
        assert resp.status_code == 200
        assert "Compare Two Runs" in resp.text

    def test_demote_button_present_for_each_baseline(
        self, app_client: TestClient
    ) -> None:
        run = _create_run(app_client, trace_id="dm1", suite="demo")
        app_client.post(f"/runs/{run['id']}/promote")
        resp = app_client.get("/dashboard/baselines")
        assert resp.status_code == 200
        assert "Demote" in resp.text
