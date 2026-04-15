"""Tests for cloud auth, CORS, rate-limiting, and /health/ready.

All tests use in-memory SQLite via the create_app factory so they run without
any external services.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from mcptest.cloud.app import create_app
from mcptest.cloud.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_KEY = "test-key-abc"
_OTHER_KEY = "other-key-xyz"

_RUN_PAYLOAD = {
    "trace_id": "tr-auth-001",
    "suite": "auth-suite",
    "case": "case-1",
    "input": "hello",
    "output": "world",
    "exit_code": 0,
    "duration_s": 1.0,
    "total_tool_calls": 0,
    "passed": True,
    "tool_calls": [],
    "run_metadata": {},
    "metric_scores": {"tool_call_accuracy": 0.9},
}


def _client(
    api_keys: str = "",
    auth_required: bool = False,
    rate_limit: int = 1000,
    cors_origins: list[str] | None = None,
) -> TestClient:
    """Build a TestClient with the given security settings."""
    settings = Settings(
        database_url="sqlite:///:memory:",
        api_keys=frozenset(k.strip() for k in api_keys.split(",") if k.strip()),
        auth_required=auth_required,
        cors_origins=cors_origins or ["*"],
        rate_limit=rate_limit,
    )
    app = create_app(settings)
    return TestClient(app, raise_server_exceptions=True)


def _fresh_payload(trace_id: str = "tr-fresh-001") -> dict:
    return {**_RUN_PAYLOAD, "trace_id": trace_id}


# ---------------------------------------------------------------------------
# API-key validation — no keys configured (open mode)
# ---------------------------------------------------------------------------


class TestNoKeysConfigured:
    """When MCPTEST_API_KEYS is empty, all endpoints are open."""

    def test_post_runs_succeeds_without_key(self) -> None:
        client = _client(api_keys="")
        resp = client.post("/runs", json=_fresh_payload("tr-nokey-001"))
        assert resp.status_code == 201

    def test_get_runs_succeeds_without_key(self) -> None:
        client = _client(api_keys="")
        resp = client.get("/runs")
        assert resp.status_code == 200

    def test_delete_run_succeeds_without_key(self) -> None:
        client = _client(api_keys="")
        create = client.post("/runs", json=_fresh_payload("tr-nokey-del"))
        run_id = create.json()["id"]
        resp = client.delete(f"/runs/{run_id}")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# API-key validation — keys configured
# ---------------------------------------------------------------------------


class TestApiKeyValidation:
    """When MCPTEST_API_KEYS is set, write endpoints require a valid key."""

    def test_post_runs_with_valid_key(self) -> None:
        client = _client(api_keys=_VALID_KEY)
        resp = client.post(
            "/runs",
            json=_fresh_payload("tr-key-valid"),
            headers={"X-API-Key": _VALID_KEY},
        )
        assert resp.status_code == 201

    def test_post_runs_missing_key_returns_401(self) -> None:
        client = _client(api_keys=_VALID_KEY)
        resp = client.post("/runs", json=_fresh_payload("tr-key-missing"))
        assert resp.status_code == 401

    def test_post_runs_wrong_key_returns_401(self) -> None:
        client = _client(api_keys=_VALID_KEY)
        resp = client.post(
            "/runs",
            json=_fresh_payload("tr-key-wrong"),
            headers={"X-API-Key": "bad-key"},
        )
        assert resp.status_code == 401

    def test_delete_run_missing_key_returns_401(self) -> None:
        # Create without key (no keys configured for this factory call)
        open_client = _client(api_keys="")
        run_id = open_client.post("/runs", json=_fresh_payload("tr-del-401")).json()["id"]

        # Now try to delete with a client that has keys configured
        auth_client = _client(api_keys=_VALID_KEY)
        # First create another run with auth
        auth_client.post(
            "/runs",
            json=_fresh_payload("tr-del-401b"),
            headers={"X-API-Key": _VALID_KEY},
        )
        # Delete without key
        resp = auth_client.delete(f"/runs/1")
        assert resp.status_code == 401

    def test_delete_run_with_valid_key(self) -> None:
        client = _client(api_keys=_VALID_KEY)
        run_id = client.post(
            "/runs",
            json=_fresh_payload("tr-del-ok"),
            headers={"X-API-Key": _VALID_KEY},
        ).json()["id"]
        resp = client.delete(f"/runs/{run_id}", headers={"X-API-Key": _VALID_KEY})
        assert resp.status_code == 204

    def test_multiple_valid_keys(self) -> None:
        client = _client(api_keys=f"{_VALID_KEY},{_OTHER_KEY}")
        for i, key in enumerate([_VALID_KEY, _OTHER_KEY]):
            resp = client.post(
                "/runs",
                json=_fresh_payload(f"tr-multikey-{i}"),
                headers={"X-API-Key": key},
            )
            assert resp.status_code == 201, f"key {key!r} should be accepted"


# ---------------------------------------------------------------------------
# Read-only endpoints open when auth_required=False
# ---------------------------------------------------------------------------


class TestReadEndpointsOpenByDefault:
    """GET endpoints work without a key unless auth_required=True."""

    def test_get_runs_open_when_not_required(self) -> None:
        client = _client(api_keys=_VALID_KEY, auth_required=False)
        resp = client.get("/runs")
        assert resp.status_code == 200

    def test_get_run_by_id_open_when_not_required(self) -> None:
        client = _client(api_keys=_VALID_KEY, auth_required=False)
        # Create a run first
        run_id = client.post(
            "/runs",
            json=_fresh_payload("tr-read-open"),
            headers={"X-API-Key": _VALID_KEY},
        ).json()["id"]
        resp = client.get(f"/runs/{run_id}")
        assert resp.status_code == 200

    def test_list_baselines_open_when_not_required(self) -> None:
        client = _client(api_keys=_VALID_KEY, auth_required=False)
        resp = client.get("/baselines")
        assert resp.status_code == 200

    def test_metrics_history_open_when_not_required(self) -> None:
        client = _client(api_keys=_VALID_KEY, auth_required=False)
        resp = client.get("/metrics/history", params={"suite": "x"})
        assert resp.status_code == 200

    def test_health_always_open(self) -> None:
        client = _client(api_keys=_VALID_KEY, auth_required=True)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_ready_always_open(self) -> None:
        client = _client(api_keys=_VALID_KEY, auth_required=True)
        resp = client.get("/health/ready")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Baseline write endpoints require auth
# ---------------------------------------------------------------------------


class TestBaselineAuthProtection:
    """POST/DELETE /runs/{id}/promote require auth when keys are configured."""

    def test_promote_missing_key_returns_401(self) -> None:
        client = _client(api_keys=_VALID_KEY)
        run_id = client.post(
            "/runs",
            json=_fresh_payload("tr-promote-401"),
            headers={"X-API-Key": _VALID_KEY},
        ).json()["id"]
        resp = client.post(f"/runs/{run_id}/promote")
        assert resp.status_code == 401

    def test_promote_with_valid_key(self) -> None:
        client = _client(api_keys=_VALID_KEY)
        run_id = client.post(
            "/runs",
            json=_fresh_payload("tr-promote-ok"),
            headers={"X-API-Key": _VALID_KEY},
        ).json()["id"]
        resp = client.post(
            f"/runs/{run_id}/promote", headers={"X-API-Key": _VALID_KEY}
        )
        assert resp.status_code == 200
        assert resp.json()["is_baseline"] is True

    def test_demote_missing_key_returns_401(self) -> None:
        client = _client(api_keys=_VALID_KEY)
        run_id = client.post(
            "/runs",
            json=_fresh_payload("tr-demote-401"),
            headers={"X-API-Key": _VALID_KEY},
        ).json()["id"]
        resp = client.delete(f"/runs/{run_id}/promote")
        assert resp.status_code == 401

    def test_demote_with_valid_key(self) -> None:
        client = _client(api_keys=_VALID_KEY)
        run_id = client.post(
            "/runs",
            json=_fresh_payload("tr-demote-ok"),
            headers={"X-API-Key": _VALID_KEY},
        ).json()["id"]
        client.post(
            f"/runs/{run_id}/promote", headers={"X-API-Key": _VALID_KEY}
        )
        resp = client.delete(
            f"/runs/{run_id}/promote", headers={"X-API-Key": _VALID_KEY}
        )
        assert resp.status_code == 200
        assert resp.json()["is_baseline"] is False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """In-memory rate limiter returns 429 after the threshold is exceeded."""

    def test_rate_limit_exceeded_returns_429(self) -> None:
        # Limit of 3 requests per minute.
        client = _client(api_keys="", rate_limit=3)
        for _ in range(3):
            resp = client.get("/health")
            assert resp.status_code == 200
        # The 4th request should be rate-limited.
        resp = client.get("/health")
        assert resp.status_code == 429

    def test_rate_limit_retry_after_header(self) -> None:
        client = _client(api_keys="", rate_limit=1)
        client.get("/health")
        resp = client.get("/health")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    def test_rate_limit_body_is_json(self) -> None:
        client = _client(api_keys="", rate_limit=1)
        client.get("/health")
        resp = client.get("/health")
        assert resp.status_code == 429
        data = resp.json()
        assert "detail" in data


# ---------------------------------------------------------------------------
# CORS headers
# ---------------------------------------------------------------------------


class TestCORSHeaders:
    """CORS headers must be present on responses."""

    def test_cors_headers_present_on_get(self) -> None:
        client = _client(cors_origins=["https://example.com"])
        resp = client.get("/health", headers={"Origin": "https://example.com"})
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers

    def test_cors_wildcard_origin(self) -> None:
        client = _client(cors_origins=["*"])
        resp = client.get("/health", headers={"Origin": "https://anything.com"})
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers

    def test_cors_preflight_options(self) -> None:
        client = _client(cors_origins=["*"])
        resp = client.options(
            "/runs",
            headers={
                "Origin": "https://dashboard.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-Key, Content-Type",
            },
        )
        # Starlette CORS returns 200 for preflight
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers


# ---------------------------------------------------------------------------
# /health/ready
# ---------------------------------------------------------------------------


class TestHealthReady:
    """GET /health/ready verifies DB connectivity."""

    def test_ready_returns_200_with_sqlite(self) -> None:
        client = _client()
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["db"] == "ok"
        assert data["service"] == "mcptest-cloud"

    def test_ready_response_has_version(self) -> None:
        client = _client()
        resp = client.get("/health/ready")
        assert "version" in resp.json()

    def test_liveness_does_not_include_db_field(self) -> None:
        client = _client()
        resp = client.get("/health")
        data = resp.json()
        assert "db" not in data
        assert data["status"] == "ok"
