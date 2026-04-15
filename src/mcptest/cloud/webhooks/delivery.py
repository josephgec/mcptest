"""HTTP delivery engine for webhook events.

Handles:
- JSON serialization of the canonical ``WebhookEventPayload`` envelope
- HMAC-SHA256 request signing (``X-MCPTest-Signature: sha256=<hex>``)
- Retry with exponential back-off (up to 3 attempts: 1s, 4s, 16s)
- Audit logging via ``WebhookDelivery`` rows
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from mcptest.cloud.webhooks.models import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = [1, 4, 16]
_RESPONSE_BODY_MAX = 1024  # truncate stored response body to 1 KB
_TIMEOUT = 10.0  # seconds per attempt


def deliver_webhook(
    db: Session,
    webhook: Webhook,
    event: str,
    data: dict,
) -> WebhookDelivery:
    """Deliver *event* to *webhook.url* and record the result.

    Retries up to ``_MAX_ATTEMPTS`` times on connection/timeout errors or
    5xx responses, with exponential back-off.  A ``WebhookDelivery`` row is
    written on the *final* attempt (success or permanent failure).

    Returns the logged ``WebhookDelivery`` record.
    """
    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    body_bytes = json.dumps(payload, default=str).encode("utf-8")
    headers = _build_headers(webhook, event, body_bytes)

    last_status: int | None = None
    last_body: str | None = None
    success = False

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(
                webhook.url,
                content=body_bytes,
                headers=headers,
                timeout=_TIMEOUT,
            )
            last_status = response.status_code
            last_body = response.text[:_RESPONSE_BODY_MAX]
            success = 200 <= response.status_code < 300

            if success:
                break

            # Retry on 5xx
            if response.status_code >= 500 and attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_SECONDS[attempt - 1])
                continue

            # Non-retryable 4xx
            break

        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning(
                "Webhook %d delivery attempt %d/%d failed: %s",
                webhook.id,
                attempt,
                _MAX_ATTEMPTS,
                exc,
            )
            last_body = str(exc)[:_RESPONSE_BODY_MAX]
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_SECONDS[attempt - 1])
            continue

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=event,
        payload=payload,
        response_status=last_status,
        response_body=last_body,
        success=success,
        attempt=attempt,
    )
    db.add(delivery)
    db.commit()
    return delivery


def _build_headers(webhook: Webhook, event: str, body: bytes) -> dict[str, str]:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-MCPTest-Event": event,
        "User-Agent": "mcptest-cloud/0.1",
    }
    if webhook.secret:
        sig = _compute_signature(webhook.secret, body)
        headers["X-MCPTest-Signature"] = f"sha256={sig}"
    return headers


def _compute_signature(secret: str, body: bytes) -> str:
    """Return the hex-encoded HMAC-SHA256 of *body* keyed with *secret*."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """Verify an incoming ``X-MCPTest-Signature`` header value.

    Args:
        secret: The shared signing secret.
        body: The raw request body bytes.
        signature: The value of the ``X-MCPTest-Signature`` header,
            expected in ``sha256=<hex>`` format.

    Returns:
        ``True`` if the signature is valid, ``False`` otherwise.
    """
    if not signature.startswith("sha256="):
        return False
    expected = _compute_signature(secret, body)
    provided = signature[len("sha256="):]
    return hmac.compare_digest(expected, provided)
