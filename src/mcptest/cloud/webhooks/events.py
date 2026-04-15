"""Webhook event types and the event dispatcher."""

from __future__ import annotations

from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from mcptest.cloud.webhooks.models import Webhook


class WebhookEvent(str, Enum):
    """Named events that webhooks can subscribe to."""

    RUN_CREATED = "run.created"
    REGRESSION_DETECTED = "regression.detected"
    BASELINE_PROMOTED = "baseline.promoted"
    BASELINE_DEMOTED = "baseline.demoted"


# Flat list of all valid event name strings, used for validation.
ALL_EVENTS: list[str] = [e.value for e in WebhookEvent]


def dispatch_event(
    db: Session,
    event: WebhookEvent,
    data: dict,
    suite: str | None = None,
) -> None:
    """Fire *event* to all matching active webhooks.

    Matching rules:
    - Webhook must be ``active=True``.
    - Webhook must have *event* in its ``events`` list.
    - If the webhook has a ``suite_filter``, the *suite* argument must match.

    Delivery is synchronous and best-effort: failures are logged in
    ``WebhookDelivery`` rows but never bubble up to the caller.

    Args:
        db: Active SQLAlchemy session.
        event: The event to dispatch.
        data: Arbitrary event payload dict (will be nested under ``data``).
        suite: The suite associated with the event (for filtering).
    """
    from mcptest.cloud.webhooks.delivery import deliver_webhook  # avoid circular

    stmt = select(Webhook).where(Webhook.active.is_(True))
    webhooks = list(db.scalars(stmt))

    event_value = event.value
    for webhook in webhooks:
        # Check event subscription
        subscribed_events: list[str] = webhook.events or []
        if event_value not in subscribed_events:
            continue

        # Check suite filter
        if webhook.suite_filter is not None:
            if suite != webhook.suite_filter:
                continue

        deliver_webhook(db, webhook, event_value, data)
