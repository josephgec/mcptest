"""Tests for GET /metrics/history endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcptest.cloud import Settings, create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


_CTR = 0


def _make_run(
    client: TestClient,
    *,
    suite: str = "smoke",
    branch: str | None = None,
    metric_scores: dict | None = None,
) -> dict:
    global _CTR
    _CTR += 1
    payload = {
        "trace_id": f"hist-{_CTR}",
        "suite": suite,
        "case": "c1",
        "input": "",
        "output": "",
        "exit_code": 0,
        "duration_s": 1.0,
        "total_tool_calls": 0,
        "passed": True,
        "tool_calls": [],
        "run_metadata": {},
        "metric_scores": metric_scores or {},
        "branch": branch,
    }
    resp = client.post("/runs", json=payload)
    assert resp.status_code == 201, resp.json()
    return resp.json()


class TestMetricHistory:
    def test_empty_when_no_runs(self, client: TestClient) -> None:
        resp = client.get("/metrics/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["points"] == []
        assert data["suite"] is None
        assert data["branch"] is None
        assert data["metric"] is None

    def test_returns_runs_in_reverse_chronological_order(self, client: TestClient) -> None:
        r1 = _make_run(client, suite="ord")
        r2 = _make_run(client, suite="ord")
        r3 = _make_run(client, suite="ord")

        resp = client.get("/metrics/history?suite=ord")
        points = resp.json()["points"]
        ids = [p["run_id"] for p in points]
        # Should be newest first — r3, r2, r1.
        assert ids == [r3["id"], r2["id"], r1["id"]]

    def test_suite_filter(self, client: TestClient) -> None:
        _make_run(client, suite="target")
        _make_run(client, suite="other")

        resp = client.get("/metrics/history?suite=target")
        data = resp.json()
        assert data["suite"] == "target"
        assert len(data["points"]) == 1

    def test_branch_filter(self, client: TestClient) -> None:
        _make_run(client, branch="main")
        _make_run(client, branch="feature-x")
        _make_run(client, branch="main")

        resp = client.get("/metrics/history?branch=main")
        data = resp.json()
        assert data["branch"] == "main"
        assert len(data["points"]) == 2

    def test_metric_filter_returns_single_metric(self, client: TestClient) -> None:
        _make_run(client, metric_scores={"tool_efficiency": 0.8, "redundancy": 0.9})

        resp = client.get("/metrics/history?metric=tool_efficiency")
        data = resp.json()
        assert data["metric"] == "tool_efficiency"
        point = data["points"][0]
        assert "tool_efficiency" in point["metric_scores"]
        assert "redundancy" not in point["metric_scores"]

    def test_metric_filter_absent_metric_gives_empty_scores(
        self, client: TestClient
    ) -> None:
        _make_run(client, metric_scores={"tool_efficiency": 0.8})

        resp = client.get("/metrics/history?metric=nonexistent")
        point = resp.json()["points"][0]
        assert point["metric_scores"] == {}

    def test_limit_param(self, client: TestClient) -> None:
        for _ in range(5):
            _make_run(client)
        resp = client.get("/metrics/history?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()["points"]) == 3

    def test_limit_max_enforced(self, client: TestClient) -> None:
        resp = client.get("/metrics/history?limit=201")
        assert resp.status_code == 422

    def test_limit_min_enforced(self, client: TestClient) -> None:
        resp = client.get("/metrics/history?limit=0")
        assert resp.status_code == 422

    def test_point_fields(self, client: TestClient) -> None:
        run = _make_run(client, branch="main", metric_scores={"tool_efficiency": 0.75})
        resp = client.get("/metrics/history")
        point = resp.json()["points"][0]
        assert point["run_id"] == run["id"]
        assert point["branch"] == "main"
        assert "created_at" in point
        assert point["metric_scores"]["tool_efficiency"] == pytest.approx(0.75)

    def test_combined_suite_and_branch_filter(self, client: TestClient) -> None:
        _make_run(client, suite="s", branch="main")
        _make_run(client, suite="s", branch="feat")
        _make_run(client, suite="other", branch="main")

        resp = client.get("/metrics/history?suite=s&branch=main")
        data = resp.json()
        assert len(data["points"]) == 1
