"""Tests for the cloud backend scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcptest.cloud import (
    Settings,
    create_app,
    make_engine,
    make_session_factory,
)
from mcptest.cloud.models import TestRun as _TestRunOrm
from mcptest.cloud.schemas import TestRunCreate as _TestRunCreate
from mcptest.cloud.schemas import TestRunOut as _TestRunOut
from mcptest.cloud.schemas import ComparisonOut as _ComparisonOut
from mcptest.cloud.db import Base, create_all


@pytest.fixture
def app_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings()
        assert s.database_url.startswith("sqlite")
        assert s.version == "0.1.0"

    def test_from_env_reads_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCPTEST_DATABASE_URL", "sqlite:///tmp/override.db")
        monkeypatch.setenv("MCPTEST_CLOUD_DEBUG", "true")
        monkeypatch.setenv("MCPTEST_CLOUD_TITLE", "custom")
        monkeypatch.setenv("MCPTEST_CLOUD_VERSION", "9.9.9")
        s = Settings.from_env()
        assert s.database_url == "sqlite:///tmp/override.db"
        assert s.debug is True
        assert s.title == "custom"
        assert s.version == "9.9.9"

    def test_from_env_defaults_when_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            "MCPTEST_DATABASE_URL",
            "MCPTEST_CLOUD_DEBUG",
            "MCPTEST_CLOUD_TITLE",
            "MCPTEST_CLOUD_VERSION",
        ):
            monkeypatch.delenv(var, raising=False)
        s = Settings.from_env()
        assert s.database_url.startswith("sqlite")
        assert s.debug is False


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


class TestDatabaseHelpers:
    def test_make_engine_and_session_factory(self, tmp_path: Path) -> None:
        engine = make_engine(f"sqlite:///{tmp_path / 'x.db'}")
        create_all(engine)
        session_factory = make_session_factory(engine)
        with session_factory() as session:
            run = _TestRunOrm(trace_id="t1", passed=True)
            session.add(run)
            session.commit()
            assert run.id is not None

    def test_sqlite_sets_check_same_thread(self) -> None:
        engine = make_engine("sqlite:///:memory:")
        assert engine.url.get_backend_name() == "sqlite"

    def test_non_sqlite_does_not_pass_check_same_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the non-sqlite branch: connect_args is left empty."""
        captured: dict = {}

        def fake_create_engine(url, **kwargs):
            captured["url"] = url
            captured["connect_args"] = kwargs.get("connect_args")
            return object()  # sentinel

        monkeypatch.setattr(
            "mcptest.cloud.db.create_engine", fake_create_engine
        )
        make_engine("postgresql+psycopg://user:pw@localhost/db")
        assert captured["connect_args"] == {}


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health(self, app_client: TestClient) -> None:
        resp = app_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "mcptest-cloud"


class TestRunsEndpoints:
    def _payload(self, trace_id: str = "t1", passed: bool = True) -> dict:
        return {
            "trace_id": trace_id,
            "suite": "smoke",
            "case": "one",
            "input": "hello",
            "output": "world",
            "exit_code": 0,
            "duration_s": 1.2,
            "total_tool_calls": 2,
            "passed": passed,
            "tool_calls": [
                {"tool": "a", "arguments": {}},
                {"tool": "b", "arguments": {"x": 1}},
            ],
            "run_metadata": {"branch": "main"},
        }

    def test_create_run(self, app_client: TestClient) -> None:
        resp = app_client.post("/runs", json=self._payload())
        assert resp.status_code == 201
        data = resp.json()
        assert data["trace_id"] == "t1"
        assert data["passed"] is True
        assert data["id"] > 0
        assert len(data["tool_calls"]) == 2

    def test_create_run_duplicate_trace_id_conflict(
        self, app_client: TestClient
    ) -> None:
        app_client.post("/runs", json=self._payload(trace_id="dup"))
        resp = app_client.post("/runs", json=self._payload(trace_id="dup"))
        assert resp.status_code == 409

    def test_list_runs_default_empty(self, app_client: TestClient) -> None:
        resp = app_client.get("/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_runs_filtered_by_passed(self, app_client: TestClient) -> None:
        app_client.post("/runs", json=self._payload("a", passed=True))
        app_client.post("/runs", json=self._payload("b", passed=False))
        app_client.post("/runs", json=self._payload("c", passed=True))

        resp = app_client.get("/runs?passed=true")
        assert resp.status_code == 200
        trace_ids = [r["trace_id"] for r in resp.json()]
        assert set(trace_ids) == {"a", "c"}

        resp = app_client.get("/runs?passed=false")
        trace_ids = [r["trace_id"] for r in resp.json()]
        assert trace_ids == ["b"]

    def test_list_runs_limit(self, app_client: TestClient) -> None:
        for i in range(5):
            app_client.post("/runs", json=self._payload(f"t{i}"))
        resp = app_client.get("/runs?limit=2")
        assert len(resp.json()) == 2

    def test_list_runs_limit_rejected_out_of_range(
        self, app_client: TestClient
    ) -> None:
        resp = app_client.get("/runs?limit=0")
        assert resp.status_code == 422

    def test_get_run_by_id(self, app_client: TestClient) -> None:
        created = app_client.post("/runs", json=self._payload()).json()
        run_id = created["id"]
        resp = app_client.get(f"/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["trace_id"] == "t1"

    def test_get_run_not_found(self, app_client: TestClient) -> None:
        resp = app_client.get("/runs/99999")
        assert resp.status_code == 404

    def test_delete_run(self, app_client: TestClient) -> None:
        created = app_client.post("/runs", json=self._payload()).json()
        run_id = created["id"]
        resp = app_client.delete(f"/runs/{run_id}")
        assert resp.status_code == 204
        assert app_client.get(f"/runs/{run_id}").status_code == 404

    def test_delete_run_not_found(self, app_client: TestClient) -> None:
        resp = app_client.delete("/runs/99999")
        assert resp.status_code == 404

    def test_create_run_rejects_empty_trace_id(
        self, app_client: TestClient
    ) -> None:
        resp = app_client.post("/runs", json={"trace_id": ""})
        assert resp.status_code == 422

    def test_create_run_with_metric_scores(self, app_client: TestClient) -> None:
        payload = self._payload()
        payload["metric_scores"] = {"tool_efficiency": 0.8, "redundancy": 0.95}
        resp = app_client.post("/runs", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["metric_scores"]["tool_efficiency"] == pytest.approx(0.8)
        assert data["metric_scores"]["redundancy"] == pytest.approx(0.95)

    def test_get_run_preserves_metric_scores(self, app_client: TestClient) -> None:
        payload = self._payload()
        payload["metric_scores"] = {"tool_efficiency": 0.75}
        created = app_client.post("/runs", json=payload).json()
        run_id = created["id"]
        resp = app_client.get(f"/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["metric_scores"]["tool_efficiency"] == pytest.approx(0.75)

    def test_metric_scores_defaults_to_empty_dict(
        self, app_client: TestClient
    ) -> None:
        resp = app_client.post("/runs", json=self._payload())
        assert resp.status_code == 201
        assert resp.json()["metric_scores"] == {}


# ---------------------------------------------------------------------------
# POST /compare endpoint
# ---------------------------------------------------------------------------


class TestCompareEndpoint:
    def _create_run(
        self,
        client: TestClient,
        trace_id: str,
        metric_scores: dict,
        passed: bool = True,
    ) -> int:
        payload = {
            "trace_id": trace_id,
            "suite": "smoke",
            "case": "one",
            "input": "",
            "output": "",
            "exit_code": 0,
            "duration_s": 1.0,
            "total_tool_calls": 1,
            "passed": passed,
            "tool_calls": [],
            "run_metadata": {},
            "metric_scores": metric_scores,
        }
        resp = client.post("/runs", json=payload)
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_compare_no_regressions(self, app_client: TestClient) -> None:
        base_id = self._create_run(
            app_client, "b1", {"tool_efficiency": 0.8, "redundancy": 0.9}
        )
        head_id = self._create_run(
            app_client, "h1", {"tool_efficiency": 0.85, "redundancy": 0.95}
        )
        resp = app_client.post("/compare", json={"base_id": base_id, "head_id": head_id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_passed"] is True
        assert data["regression_count"] == 0
        assert data["base_id"] == base_id
        assert data["head_id"] == head_id

    def test_compare_with_regression(self, app_client: TestClient) -> None:
        base_id = self._create_run(
            app_client, "b2", {"tool_efficiency": 0.9}
        )
        head_id = self._create_run(
            app_client, "h2", {"tool_efficiency": 0.5}  # drop of 0.4 > threshold 0.1
        )
        resp = app_client.post("/compare", json={"base_id": base_id, "head_id": head_id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_passed"] is False
        assert data["regression_count"] == 1
        deltas = data["deltas"]
        assert len(deltas) == 1
        assert deltas[0]["name"] == "tool_efficiency"
        assert deltas[0]["regressed"] is True

    def test_compare_with_custom_threshold(self, app_client: TestClient) -> None:
        base_id = self._create_run(app_client, "b3", {"tool_efficiency": 0.9})
        head_id = self._create_run(app_client, "h3", {"tool_efficiency": 0.85})  # drop 0.05
        # Default threshold 0.1 — not a regression.
        resp = app_client.post(
            "/compare",
            json={"base_id": base_id, "head_id": head_id},
        )
        assert resp.json()["overall_passed"] is True
        # With tight threshold 0.02 — should become a regression.
        resp2 = app_client.post(
            "/compare",
            json={
                "base_id": base_id,
                "head_id": head_id,
                "thresholds": {"tool_efficiency": 0.02},
            },
        )
        assert resp2.json()["overall_passed"] is False

    def test_compare_base_not_found(self, app_client: TestClient) -> None:
        head_id = self._create_run(app_client, "h4", {})
        resp = app_client.post("/compare", json={"base_id": 99999, "head_id": head_id})
        assert resp.status_code == 404

    def test_compare_head_not_found(self, app_client: TestClient) -> None:
        base_id = self._create_run(app_client, "b5", {})
        resp = app_client.post("/compare", json={"base_id": base_id, "head_id": 99999})
        assert resp.status_code == 404

    def test_compare_empty_metric_scores(self, app_client: TestClient) -> None:
        base_id = self._create_run(app_client, "b6", {})
        head_id = self._create_run(app_client, "h6", {})
        resp = app_client.post("/compare", json={"base_id": base_id, "head_id": head_id})
        assert resp.status_code == 200
        assert resp.json()["deltas"] == []
        assert resp.json()["overall_passed"] is True

    def test_compare_delta_fields(self, app_client: TestClient) -> None:
        base_id = self._create_run(app_client, "b7", {"tool_efficiency": 0.8})
        head_id = self._create_run(app_client, "h7", {"tool_efficiency": 0.6})
        resp = app_client.post("/compare", json={"base_id": base_id, "head_id": head_id})
        delta = resp.json()["deltas"][0]
        assert delta["name"] == "tool_efficiency"
        assert delta["base_score"] == pytest.approx(0.8)
        assert delta["head_score"] == pytest.approx(0.6)
        assert delta["delta"] == pytest.approx(-0.2)
        assert delta["regressed"] is True
        assert "label" in delta

    def test_compare_label_from_registry(self, app_client: TestClient) -> None:
        base_id = self._create_run(app_client, "b8", {"tool_efficiency": 0.9})
        head_id = self._create_run(app_client, "h8", {"tool_efficiency": 0.9})
        resp = app_client.post("/compare", json={"base_id": base_id, "head_id": head_id})
        delta = resp.json()["deltas"][0]
        # tool_efficiency.label == "Tool Efficiency"
        assert delta["label"] == "Tool Efficiency"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_test_run_create_defaults(self) -> None:
        c = _TestRunCreate(trace_id="x")
        assert c.passed is True
        assert c.tool_calls == []
        assert c.run_metadata == {}

    def test_test_run_out_from_attributes(self) -> None:
        from datetime import datetime, timezone

        run = _TestRunOrm(
            id=5,
            trace_id="x",
            input="i",
            output="o",
            exit_code=0,
            duration_s=0.5,
            total_tool_calls=1,
            passed=True,
            tool_calls=[],
            run_metadata={},
            created_at=datetime.now(timezone.utc),
        )
        out = _TestRunOut.model_validate(run)
        assert out.id == 5
        assert out.trace_id == "x"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestCreateApp:
    def test_defaults_to_env_settings(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(
            "MCPTEST_DATABASE_URL", f"sqlite:///{tmp_path / 'env.db'}"
        )
        app = create_app()
        assert app.title == "mcptest cloud"

    def test_accepts_custom_settings(self, tmp_path: Path) -> None:
        settings = Settings(
            database_url=f"sqlite:///{tmp_path / 'custom.db'}",
            title="custom title",
            version="2.0",
        )
        app = create_app(settings)
        assert app.title == "custom title"
        assert app.version == "2.0"
