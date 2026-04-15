"""Tests for baselines router: promote/demote, list, and auto-compare check."""

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RUN_COUNTER = 0


def _make_run(
    client: TestClient,
    *,
    suite: str = "smoke",
    case: str = "one",
    metric_scores: dict | None = None,
    branch: str | None = None,
    git_sha: str | None = None,
    trace_id: str | None = None,
) -> dict:
    global _RUN_COUNTER
    _RUN_COUNTER += 1
    payload = {
        "trace_id": trace_id or f"trace-{_RUN_COUNTER}",
        "suite": suite,
        "case": case,
        "input": "",
        "output": "",
        "exit_code": 0,
        "duration_s": 1.0,
        "total_tool_calls": 1,
        "passed": True,
        "tool_calls": [],
        "run_metadata": {},
        "metric_scores": metric_scores or {},
        "branch": branch,
        "git_sha": git_sha,
    }
    resp = client.post("/runs", json=payload)
    assert resp.status_code == 201, resp.json()
    return resp.json()


# ---------------------------------------------------------------------------
# POST /runs/{id}/promote
# ---------------------------------------------------------------------------


class TestPromote:
    def test_promote_sets_is_baseline(self, client: TestClient) -> None:
        run = _make_run(client)
        resp = client.post(f"/runs/{run['id']}/promote")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_baseline"] is True
        assert data["id"] == run["id"]
        assert data["suite"] == run["suite"]
        assert "message" in data

    def test_promote_updates_run_record(self, client: TestClient) -> None:
        run = _make_run(client)
        client.post(f"/runs/{run['id']}/promote")
        updated = client.get(f"/runs/{run['id']}").json()
        assert updated["is_baseline"] is True

    def test_promote_only_one_baseline_per_suite(self, client: TestClient) -> None:
        """Promoting a second run for the same suite demotes the first."""
        run1 = _make_run(client, suite="alpha")
        run2 = _make_run(client, suite="alpha")
        client.post(f"/runs/{run1['id']}/promote")
        client.post(f"/runs/{run2['id']}/promote")

        assert client.get(f"/runs/{run1['id']}").json()["is_baseline"] is False
        assert client.get(f"/runs/{run2['id']}").json()["is_baseline"] is True

    def test_promote_different_suites_are_independent(self, client: TestClient) -> None:
        """Each suite has its own baseline slot."""
        run_a = _make_run(client, suite="suite-a")
        run_b = _make_run(client, suite="suite-b")
        client.post(f"/runs/{run_a['id']}/promote")
        client.post(f"/runs/{run_b['id']}/promote")

        assert client.get(f"/runs/{run_a['id']}").json()["is_baseline"] is True
        assert client.get(f"/runs/{run_b['id']}").json()["is_baseline"] is True

    def test_promote_not_found(self, client: TestClient) -> None:
        resp = client.post("/runs/99999/promote")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /runs/{id}/promote
# ---------------------------------------------------------------------------


class TestDemote:
    def test_demote_clears_flag(self, client: TestClient) -> None:
        run = _make_run(client)
        client.post(f"/runs/{run['id']}/promote")
        resp = client.delete(f"/runs/{run['id']}/promote")
        assert resp.status_code == 200
        assert resp.json()["is_baseline"] is False

    def test_demote_idempotent(self, client: TestClient) -> None:
        """Demoting an already-demoted run is fine."""
        run = _make_run(client)
        resp = client.delete(f"/runs/{run['id']}/promote")
        assert resp.status_code == 200
        assert resp.json()["is_baseline"] is False

    def test_demote_not_found(self, client: TestClient) -> None:
        resp = client.delete("/runs/99999/promote")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /baselines
# ---------------------------------------------------------------------------


class TestListBaselines:
    def test_empty_when_none_promoted(self, client: TestClient) -> None:
        _make_run(client)
        resp = client.get("/baselines")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_promoted_runs(self, client: TestClient) -> None:
        r1 = _make_run(client, suite="s1")
        r2 = _make_run(client, suite="s2")
        client.post(f"/runs/{r1['id']}/promote")
        client.post(f"/runs/{r2['id']}/promote")

        resp = client.get("/baselines")
        assert resp.status_code == 200
        ids = {r["id"] for r in resp.json()}
        assert ids == {r1["id"], r2["id"]}

    def test_suite_filter(self, client: TestClient) -> None:
        r1 = _make_run(client, suite="target")
        r2 = _make_run(client, suite="other")
        client.post(f"/runs/{r1['id']}/promote")
        client.post(f"/runs/{r2['id']}/promote")

        resp = client.get("/baselines?suite=target")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == r1["id"]

    def test_suite_filter_no_match(self, client: TestClient) -> None:
        r = _make_run(client, suite="existing")
        client.post(f"/runs/{r['id']}/promote")
        resp = client.get("/baselines?suite=nonexistent")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_not_listed_after_demotion(self, client: TestClient) -> None:
        run = _make_run(client)
        client.post(f"/runs/{run['id']}/promote")
        client.delete(f"/runs/{run['id']}/promote")
        resp = client.get("/baselines")
        assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /runs/{id}/check — auto-compare
# ---------------------------------------------------------------------------


class TestCheckRun:
    def test_no_baseline_returns_no_baseline_status(self, client: TestClient) -> None:
        head = _make_run(client, suite="fresh", metric_scores={"tool_efficiency": 0.8})
        resp = client.post(f"/runs/{head['id']}/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_baseline"
        assert data["baseline_id"] is None
        assert data["head_id"] == head["id"]
        assert data["deltas"] == []
        assert data["overall_passed"] is True

    def test_pass_when_no_regression(self, client: TestClient) -> None:
        base = _make_run(client, suite="suite1", metric_scores={"tool_efficiency": 0.8})
        client.post(f"/runs/{base['id']}/promote")
        head = _make_run(client, suite="suite1", metric_scores={"tool_efficiency": 0.85})

        resp = client.post(f"/runs/{head['id']}/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pass"
        assert data["overall_passed"] is True
        assert data["regression_count"] == 0
        assert data["baseline_id"] == base["id"]
        assert data["head_id"] == head["id"]

    def test_fail_when_regression_detected(self, client: TestClient) -> None:
        base = _make_run(client, suite="suite2", metric_scores={"tool_efficiency": 0.9})
        client.post(f"/runs/{base['id']}/promote")
        head = _make_run(client, suite="suite2", metric_scores={"tool_efficiency": 0.5})

        resp = client.post(f"/runs/{head['id']}/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "fail"
        assert data["overall_passed"] is False
        assert data["regression_count"] == 1
        deltas = data["deltas"]
        assert len(deltas) == 1
        assert deltas[0]["name"] == "tool_efficiency"
        assert deltas[0]["regressed"] is True

    def test_check_returns_baseline_branch(self, client: TestClient) -> None:
        base = _make_run(
            client, suite="suite3", metric_scores={"tool_efficiency": 0.8}, branch="main"
        )
        client.post(f"/runs/{base['id']}/promote")
        head = _make_run(client, suite="suite3", metric_scores={"tool_efficiency": 0.85})

        data = client.post(f"/runs/{head['id']}/check").json()
        assert data["baseline_branch"] == "main"

    def test_check_delta_fields(self, client: TestClient) -> None:
        base = _make_run(client, suite="suite4", metric_scores={"tool_efficiency": 0.8})
        client.post(f"/runs/{base['id']}/promote")
        head = _make_run(client, suite="suite4", metric_scores={"tool_efficiency": 0.6})

        data = client.post(f"/runs/{head['id']}/check").json()
        d = data["deltas"][0]
        assert d["base_score"] == pytest.approx(0.8)
        assert d["head_score"] == pytest.approx(0.6)
        assert d["delta"] == pytest.approx(-0.2)
        assert d["regressed"] is True

    def test_check_ignores_self_as_baseline(self, client: TestClient) -> None:
        """A run promoted as its own baseline should compare against the *previous*
        baseline, not itself — because the query excludes run_id == baseline's id."""
        run = _make_run(client, suite="suite5", metric_scores={"tool_efficiency": 0.9})
        client.post(f"/runs/{run['id']}/promote")

        # Checking this run (which IS the baseline) should get no_baseline
        # since there's no *other* baseline for the same suite.
        resp = client.post(f"/runs/{run['id']}/check")
        data = resp.json()
        assert data["status"] == "no_baseline"

    def test_check_not_found(self, client: TestClient) -> None:
        resp = client.post("/runs/99999/check")
        assert resp.status_code == 404

    def test_check_multiple_metrics(self, client: TestClient) -> None:
        scores_base = {"tool_efficiency": 0.9, "redundancy": 0.8}
        scores_head = {"tool_efficiency": 0.5, "redundancy": 0.85}  # te regresses

        base = _make_run(client, suite="multi", metric_scores=scores_base)
        client.post(f"/runs/{base['id']}/promote")
        head = _make_run(client, suite="multi", metric_scores=scores_head)

        data = client.post(f"/runs/{head['id']}/check").json()
        assert data["status"] == "fail"
        assert data["regression_count"] == 1
        regressed_names = [d["name"] for d in data["deltas"] if d["regressed"]]
        assert regressed_names == ["tool_efficiency"]
