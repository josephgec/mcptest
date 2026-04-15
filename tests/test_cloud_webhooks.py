"""Tests for the webhook subsystem.

Covers:
- CRUD endpoints (create/list/get/update/delete)
- Event validation (unknown names rejected)
- HMAC signature generation and verification
- Delivery engine (mock HTTP server via respx / httpx mock transport)
- Retry behavior on 5xx / connection errors
- Delivery audit logging (WebhookDelivery rows)
- suite_filter matching
- dispatch_event integration
- regression.detected fires on check_run regression
- baseline events fire on promote/demote
- run.created fires on POST /runs
- Dashboard webhook page renders
- Webhook test endpoint (POST /webhooks/{id}/test)
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from mcptest.cloud import Settings, create_app
from mcptest.cloud.webhooks.delivery import verify_signature, _compute_signature
from mcptest.cloud.webhooks.events import ALL_EVENTS, WebhookEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


_RUN_COUNTER = 0


def _make_run(
    client: TestClient,
    *,
    suite: str = "smoke",
    case: str = "one",
    metric_scores: dict | None = None,
    branch: str | None = None,
    passed: bool = True,
    trace_id: str | None = None,
) -> dict:
    global _RUN_COUNTER
    _RUN_COUNTER += 1
    payload = {
        "trace_id": trace_id or f"trace-wh-{_RUN_COUNTER}",
        "suite": suite,
        "case": case,
        "input": "",
        "output": "",
        "exit_code": 0,
        "duration_s": 1.0,
        "total_tool_calls": 1,
        "passed": passed,
        "tool_calls": [],
        "run_metadata": {},
        "metric_scores": metric_scores or {},
        "branch": branch,
    }
    resp = client.post("/runs", json=payload)
    assert resp.status_code == 201, resp.json()
    return resp.json()


def _make_webhook(
    client: TestClient,
    *,
    url: str = "https://example.com/hook",
    events: list[str] | None = None,
    secret: str | None = None,
    suite_filter: str | None = None,
    active: bool = True,
) -> dict:
    payload: dict = {
        "url": url,
        "events": events if events is not None else ["run.created"],
        "active": active,
    }
    if secret is not None:
        payload["secret"] = secret
    if suite_filter is not None:
        payload["suite_filter"] = suite_filter
    resp = client.post("/webhooks", json=payload)
    assert resp.status_code == 201, resp.json()
    return resp.json()


# ---------------------------------------------------------------------------
# CRUD — create
# ---------------------------------------------------------------------------


class TestCreateWebhook:
    def test_create_returns_201(self, client: TestClient) -> None:
        resp = client.post(
            "/webhooks",
            json={"url": "https://example.com/hook", "events": ["run.created"]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["url"] == "https://example.com/hook"
        assert data["events"] == ["run.created"]
        assert data["active"] is True
        assert data["secret"] is None
        assert "id" in data
        assert "created_at" in data

    def test_create_with_all_fields(self, client: TestClient) -> None:
        resp = client.post(
            "/webhooks",
            json={
                "url": "https://hooks.example.com/rcv",
                "secret": "mysecret",
                "events": ["regression.detected", "baseline.promoted"],
                "suite_filter": "smoke",
                "active": False,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["secret"] == "mysecret"
        assert sorted(data["events"]) == sorted(["regression.detected", "baseline.promoted"])
        assert data["suite_filter"] == "smoke"
        assert data["active"] is False

    def test_create_rejects_unknown_event(self, client: TestClient) -> None:
        resp = client.post(
            "/webhooks",
            json={"url": "https://example.com/hook", "events": ["not.a.real.event"]},
        )
        assert resp.status_code == 422

    def test_create_rejects_mixed_invalid_events(self, client: TestClient) -> None:
        resp = client.post(
            "/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["run.created", "totally.fake"],
            },
        )
        assert resp.status_code == 422

    def test_create_accepts_all_valid_events(self, client: TestClient) -> None:
        resp = client.post(
            "/webhooks",
            json={"url": "https://example.com/hook", "events": ALL_EVENTS},
        )
        assert resp.status_code == 201
        assert sorted(resp.json()["events"]) == sorted(ALL_EVENTS)


# ---------------------------------------------------------------------------
# CRUD — list / get
# ---------------------------------------------------------------------------


class TestListGetWebhook:
    def test_list_empty(self, client: TestClient) -> None:
        resp = client.get("/webhooks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created(self, client: TestClient) -> None:
        _make_webhook(client, url="https://a.example.com/1")
        _make_webhook(client, url="https://b.example.com/2")
        resp = client.get("/webhooks")
        assert resp.status_code == 200
        urls = {w["url"] for w in resp.json()}
        assert "https://a.example.com/1" in urls
        assert "https://b.example.com/2" in urls

    def test_get_existing(self, client: TestClient) -> None:
        wh = _make_webhook(client)
        resp = client.get(f"/webhooks/{wh['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == wh["id"]

    def test_get_not_found(self, client: TestClient) -> None:
        resp = client.get("/webhooks/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CRUD — update
# ---------------------------------------------------------------------------


class TestUpdateWebhook:
    def test_update_url(self, client: TestClient) -> None:
        wh = _make_webhook(client)
        resp = client.patch(
            f"/webhooks/{wh['id']}", json={"url": "https://new.example.com/hook"}
        )
        assert resp.status_code == 200
        assert resp.json()["url"] == "https://new.example.com/hook"

    def test_update_events(self, client: TestClient) -> None:
        wh = _make_webhook(client, events=["run.created"])
        resp = client.patch(
            f"/webhooks/{wh['id']}", json={"events": ["regression.detected"]}
        )
        assert resp.status_code == 200
        assert resp.json()["events"] == ["regression.detected"]

    def test_update_active(self, client: TestClient) -> None:
        wh = _make_webhook(client, active=True)
        resp = client.patch(f"/webhooks/{wh['id']}", json={"active": False})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    def test_update_rejects_unknown_events(self, client: TestClient) -> None:
        wh = _make_webhook(client)
        resp = client.patch(
            f"/webhooks/{wh['id']}", json={"events": ["bad.event"]}
        )
        assert resp.status_code == 422

    def test_update_not_found(self, client: TestClient) -> None:
        resp = client.patch("/webhooks/99999", json={"active": False})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CRUD — delete
# ---------------------------------------------------------------------------


class TestDeleteWebhook:
    def test_delete_removes_webhook(self, client: TestClient) -> None:
        wh = _make_webhook(client)
        resp = client.delete(f"/webhooks/{wh['id']}")
        assert resp.status_code == 204
        assert client.get(f"/webhooks/{wh['id']}").status_code == 404

    def test_delete_not_found(self, client: TestClient) -> None:
        resp = client.delete("/webhooks/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# HMAC signature
# ---------------------------------------------------------------------------


class TestSignature:
    def test_compute_signature_is_hmac_sha256(self) -> None:
        secret = "mysecret"
        body = b'{"event":"test"}'
        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        assert _compute_signature(secret, body) == expected

    def test_verify_signature_valid(self) -> None:
        secret = "s3cr3t"
        body = b"hello world"
        sig = f"sha256={_compute_signature(secret, body)}"
        assert verify_signature(secret, body, sig) is True

    def test_verify_signature_wrong_body(self) -> None:
        secret = "s3cr3t"
        body = b"hello world"
        sig = f"sha256={_compute_signature(secret, b'other body')}"
        assert verify_signature(secret, body, sig) is False

    def test_verify_signature_missing_prefix(self) -> None:
        secret = "s3cr3t"
        body = b"hello"
        raw_hex = _compute_signature(secret, body)
        assert verify_signature(secret, body, raw_hex) is False

    def test_verify_signature_wrong_secret(self) -> None:
        body = b"payload"
        sig = f"sha256={_compute_signature('correct', body)}"
        assert verify_signature("wrong", body, sig) is False


# ---------------------------------------------------------------------------
# Delivery engine
# ---------------------------------------------------------------------------


class TestDelivery:
    def test_delivers_json_payload(self, client: TestClient, tmp_path: Path) -> None:
        """deliver_webhook POSTs valid JSON to the target URL."""
        received: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            received.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            wh = _make_webhook(client, url="https://target.example.com/hook")
            # Trigger delivery via test endpoint
            resp = client.post(f"/webhooks/{wh['id']}/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert len(received) == 1
        payload = received[0]
        assert payload["event"] == "test.ping"
        assert "timestamp" in payload
        assert "data" in payload

    def test_signature_header_sent_when_secret_set(self, client: TestClient) -> None:
        captured_headers: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            captured_headers.append(dict(headers))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            wh = _make_webhook(client, secret="topsecret")
            client.post(f"/webhooks/{wh['id']}/test")

        assert len(captured_headers) == 1
        sig_header = captured_headers[0].get("X-MCPTest-Signature", "")
        assert sig_header.startswith("sha256=")

    def test_no_signature_header_without_secret(self, client: TestClient) -> None:
        captured_headers: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            captured_headers.append(dict(headers))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            wh = _make_webhook(client)  # no secret
            client.post(f"/webhooks/{wh['id']}/test")

        assert "X-MCPTest-Signature" not in captured_headers[0]

    def test_delivery_logged_on_success(self, client: TestClient) -> None:
        with patch(
            "mcptest.cloud.webhooks.delivery.httpx.post",
            return_value=httpx.Response(200, text="ok"),
        ):
            wh = _make_webhook(client)
            client.post(f"/webhooks/{wh['id']}/test")

        deliveries = client.get(f"/webhooks/{wh['id']}/deliveries").json()
        assert len(deliveries) == 1
        assert deliveries[0]["success"] is True
        assert deliveries[0]["response_status"] == 200
        assert deliveries[0]["event"] == "test.ping"

    def test_delivery_logged_on_failure(self, client: TestClient) -> None:
        with patch(
            "mcptest.cloud.webhooks.delivery.httpx.post",
            return_value=httpx.Response(500, text="server error"),
        ):
            wh = _make_webhook(client)
            client.post(f"/webhooks/{wh['id']}/test")

        deliveries = client.get(f"/webhooks/{wh['id']}/deliveries").json()
        assert len(deliveries) == 1
        assert deliveries[0]["success"] is False
        assert deliveries[0]["response_status"] == 500

    def test_retry_on_500(self, client: TestClient) -> None:
        """Delivery retries up to 3 times on 5xx responses."""
        call_count = 0

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(500, text="error")
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            with patch("mcptest.cloud.webhooks.delivery.time.sleep"):
                wh = _make_webhook(client)
                resp = client.post(f"/webhooks/{wh['id']}/test")

        assert resp.json()["success"] is True
        assert call_count == 3

    def test_retry_on_connection_error(self, client: TestClient) -> None:
        call_count = 0

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            with patch("mcptest.cloud.webhooks.delivery.time.sleep"):
                wh = _make_webhook(client)
                resp = client.post(f"/webhooks/{wh['id']}/test")

        assert resp.json()["success"] is True
        assert call_count == 2

    def test_no_retry_on_4xx(self, client: TestClient) -> None:
        call_count = 0

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            return httpx.Response(400, text="bad request")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            wh = _make_webhook(client)
            client.post(f"/webhooks/{wh['id']}/test")

        # Should not retry on 4xx
        assert call_count == 1


# ---------------------------------------------------------------------------
# suite_filter matching
# ---------------------------------------------------------------------------


class TestSuiteFilter:
    def test_webhook_fires_for_matching_suite(self, client: TestClient) -> None:
        fired: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            fired.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            _make_webhook(
                client,
                events=["run.created"],
                suite_filter="target-suite",
            )
            _make_run(client, suite="target-suite")

        assert len(fired) == 1

    def test_webhook_skips_non_matching_suite(self, client: TestClient) -> None:
        fired: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            fired.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            _make_webhook(
                client,
                events=["run.created"],
                suite_filter="target-suite",
            )
            _make_run(client, suite="other-suite")

        assert len(fired) == 0

    def test_no_filter_fires_for_all_suites(self, client: TestClient) -> None:
        fired: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            fired.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            _make_webhook(client, events=["run.created"])  # no suite_filter
            _make_run(client, suite="suite-a")
            _make_run(client, suite="suite-b")

        assert len(fired) == 2


# ---------------------------------------------------------------------------
# Event integration — run.created
# ---------------------------------------------------------------------------


class TestRunCreatedEvent:
    def test_run_created_fires_on_post(self, client: TestClient) -> None:
        fired: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            fired.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            _make_webhook(client, events=["run.created"])
            _make_run(client, suite="smoke")

        assert len(fired) == 1
        assert fired[0]["event"] == "run.created"
        assert fired[0]["data"]["suite"] == "smoke"

    def test_run_created_not_fired_for_unsubscribed_webhook(
        self, client: TestClient
    ) -> None:
        fired: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            fired.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            _make_webhook(client, events=["regression.detected"])
            _make_run(client, suite="smoke")

        assert len(fired) == 0

    def test_inactive_webhook_not_fired(self, client: TestClient) -> None:
        fired: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            fired.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            _make_webhook(client, events=["run.created"], active=False)
            _make_run(client, suite="smoke")

        assert len(fired) == 0


# ---------------------------------------------------------------------------
# Event integration — baseline events
# ---------------------------------------------------------------------------


class TestBaselineEvents:
    def test_baseline_promoted_event_fires(self, client: TestClient) -> None:
        fired: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            fired.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            _make_webhook(client, events=["baseline.promoted"])
            run = _make_run(client)
            client.post(f"/runs/{run['id']}/promote")

        assert any(p["event"] == "baseline.promoted" for p in fired)

    def test_baseline_demoted_event_fires(self, client: TestClient) -> None:
        fired: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            fired.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            _make_webhook(client, events=["baseline.demoted"])
            run = _make_run(client)
            client.post(f"/runs/{run['id']}/promote")
            client.delete(f"/runs/{run['id']}/promote")

        assert any(p["event"] == "baseline.demoted" for p in fired)


# ---------------------------------------------------------------------------
# Event integration — regression.detected
# ---------------------------------------------------------------------------


class TestRegressionDetectedEvent:
    def test_regression_detected_fires_on_check_fail(
        self, client: TestClient
    ) -> None:
        fired: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            fired.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            _make_webhook(client, events=["regression.detected"])
            base = _make_run(
                client, suite="reg-suite", metric_scores={"tool_efficiency": 0.9}
            )
            client.post(f"/runs/{base['id']}/promote")
            head = _make_run(
                client, suite="reg-suite", metric_scores={"tool_efficiency": 0.5}
            )
            client.post(f"/runs/{head['id']}/check")

        regression_events = [p for p in fired if p["event"] == "regression.detected"]
        assert len(regression_events) == 1
        assert regression_events[0]["data"]["suite"] == "reg-suite"
        assert regression_events[0]["data"]["regression_count"] == 1

    def test_regression_detected_not_fired_on_pass(self, client: TestClient) -> None:
        fired: list[dict] = []

        def mock_post(url, content, headers, timeout):  # noqa: ARG001
            fired.append(json.loads(content))
            return httpx.Response(200, text="ok")

        with patch("mcptest.cloud.webhooks.delivery.httpx.post", side_effect=mock_post):
            _make_webhook(client, events=["regression.detected"])
            base = _make_run(
                client, suite="pass-suite", metric_scores={"tool_efficiency": 0.7}
            )
            client.post(f"/runs/{base['id']}/promote")
            head = _make_run(
                client, suite="pass-suite", metric_scores={"tool_efficiency": 0.9}
            )
            client.post(f"/runs/{head['id']}/check")

        regression_events = [p for p in fired if p["event"] == "regression.detected"]
        assert len(regression_events) == 0


# ---------------------------------------------------------------------------
# Test endpoint
# ---------------------------------------------------------------------------


class TestWebhookTestEndpoint:
    def test_test_endpoint_success(self, client: TestClient) -> None:
        with patch(
            "mcptest.cloud.webhooks.delivery.httpx.post",
            return_value=httpx.Response(200, text="ok"),
        ):
            wh = _make_webhook(client)
            resp = client.post(f"/webhooks/{wh['id']}/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status_code"] == 200

    def test_test_endpoint_failure(self, client: TestClient) -> None:
        with patch(
            "mcptest.cloud.webhooks.delivery.httpx.post",
            return_value=httpx.Response(404, text="not found"),
        ):
            wh = _make_webhook(client)
            resp = client.post(f"/webhooks/{wh['id']}/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    def test_test_endpoint_not_found(self, client: TestClient) -> None:
        resp = client.post("/webhooks/99999/test")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delivery history endpoint
# ---------------------------------------------------------------------------


class TestDeliveryHistory:
    def test_deliveries_listed(self, client: TestClient) -> None:
        with patch(
            "mcptest.cloud.webhooks.delivery.httpx.post",
            return_value=httpx.Response(200, text="ok"),
        ):
            wh = _make_webhook(client)
            client.post(f"/webhooks/{wh['id']}/test")
            client.post(f"/webhooks/{wh['id']}/test")

        resp = client.get(f"/webhooks/{wh['id']}/deliveries")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_deliveries_not_found(self, client: TestClient) -> None:
        resp = client.get("/webhooks/99999/deliveries")
        assert resp.status_code == 404

    def test_deliveries_respect_limit(self, client: TestClient) -> None:
        with patch(
            "mcptest.cloud.webhooks.delivery.httpx.post",
            return_value=httpx.Response(200, text="ok"),
        ):
            wh = _make_webhook(client)
            for _ in range(5):
                client.post(f"/webhooks/{wh['id']}/test")

        resp = client.get(f"/webhooks/{wh['id']}/deliveries?limit=3")
        assert len(resp.json()) == 3


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestDashboardWebhooksPage:
    def test_dashboard_webhooks_renders(self, client: TestClient) -> None:
        resp = client.get("/dashboard/webhooks")
        assert resp.status_code == 200
        assert b"Webhooks" in resp.content

    def test_dashboard_webhooks_shows_webhook(self, client: TestClient) -> None:
        _make_webhook(client, url="https://unique-hook-url.example.com/receive")
        resp = client.get("/dashboard/webhooks")
        assert resp.status_code == 200
        assert b"unique-hook-url.example.com" in resp.content

    def test_dashboard_webhooks_shows_events(self, client: TestClient) -> None:
        resp = client.get("/dashboard/webhooks")
        assert resp.status_code == 200
        # Event reference section should list known events
        assert b"run.created" in resp.content
        assert b"regression.detected" in resp.content


# ---------------------------------------------------------------------------
# All events constant
# ---------------------------------------------------------------------------


class TestAllEvents:
    def test_all_events_contains_four_events(self) -> None:
        assert len(ALL_EVENTS) == 4

    def test_all_events_contains_expected_names(self) -> None:
        assert "run.created" in ALL_EVENTS
        assert "regression.detected" in ALL_EVENTS
        assert "baseline.promoted" in ALL_EVENTS
        assert "baseline.demoted" in ALL_EVENTS

    def test_webhook_event_enum_values_match_all_events(self) -> None:
        enum_values = {e.value for e in WebhookEvent}
        assert enum_values == set(ALL_EVENTS)
