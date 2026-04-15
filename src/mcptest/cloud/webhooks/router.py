"""`/webhooks` endpoints — CRUD, test delivery, and delivery history."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from mcptest.cloud.auth import require_auth
from mcptest.cloud.schemas import (
    WebhookCreate,
    WebhookDeliveryOut,
    WebhookOut,
    WebhookTestOut,
    WebhookUpdate,
)
from mcptest.cloud.webhooks.events import ALL_EVENTS
from mcptest.cloud.webhooks.models import Webhook, WebhookDelivery

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def get_db() -> Session:  # pragma: no cover
    """Placeholder dependency — overridden by the app factory at startup."""
    raise NotImplementedError(
        "get_db must be overridden via app.dependency_overrides"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_events(events: list[str]) -> None:
    """Raise 422 if any event name is not in ALL_EVENTS."""
    unknown = [e for e in events if e not in ALL_EVENTS]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown event names: {unknown}. Valid: {ALL_EVENTS}",
        )


def _get_webhook_or_404(db: Session, webhook_id: int) -> Webhook:
    wh = db.get(Webhook, webhook_id)
    if wh is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no webhook with id {webhook_id}",
        )
    return wh


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=WebhookOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
)
def create_webhook(
    payload: WebhookCreate,
    db: Annotated[Session, Depends(get_db)],
) -> Webhook:
    _validate_events(payload.events)
    wh = Webhook(
        url=payload.url,
        secret=payload.secret,
        events=payload.events,
        suite_filter=payload.suite_filter,
        active=payload.active,
    )
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


@router.get("", response_model=list[WebhookOut], dependencies=[Depends(require_auth)])
def list_webhooks(
    db: Annotated[Session, Depends(get_db)],
) -> list[Webhook]:
    return list(db.scalars(select(Webhook).order_by(Webhook.created_at.desc())))


@router.get(
    "/{webhook_id}",
    response_model=WebhookOut,
    dependencies=[Depends(require_auth)],
)
def get_webhook(
    webhook_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> Webhook:
    return _get_webhook_or_404(db, webhook_id)


@router.patch(
    "/{webhook_id}",
    response_model=WebhookOut,
    dependencies=[Depends(require_auth)],
)
def update_webhook(
    webhook_id: int,
    payload: WebhookUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> Webhook:
    wh = _get_webhook_or_404(db, webhook_id)
    if payload.events is not None:
        _validate_events(payload.events)
        wh.events = payload.events
    if payload.url is not None:
        wh.url = payload.url
    if payload.secret is not None:
        wh.secret = payload.secret
    if payload.suite_filter is not None:
        wh.suite_filter = payload.suite_filter
    if payload.active is not None:
        wh.active = payload.active
    db.commit()
    db.refresh(wh)
    return wh


@router.delete(
    "/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_auth)],
)
def delete_webhook(
    webhook_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    wh = _get_webhook_or_404(db, webhook_id)
    db.delete(wh)
    db.commit()


# ---------------------------------------------------------------------------
# Test delivery
# ---------------------------------------------------------------------------


@router.post(
    "/{webhook_id}/test",
    response_model=WebhookTestOut,
    dependencies=[Depends(require_auth)],
)
def test_webhook(
    webhook_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> WebhookTestOut:
    """Send a ``test.ping`` event to the webhook URL and return the result."""
    from mcptest.cloud.webhooks.delivery import deliver_webhook

    wh = _get_webhook_or_404(db, webhook_id)
    delivery = deliver_webhook(db, wh, "test.ping", {"message": "This is a test ping from mcptest cloud."})
    return WebhookTestOut(
        success=delivery.success,
        status_code=delivery.response_status,
        message="Delivery succeeded." if delivery.success else f"Delivery failed after {delivery.attempt} attempt(s).",
    )


# ---------------------------------------------------------------------------
# Delivery history
# ---------------------------------------------------------------------------


@router.get(
    "/{webhook_id}/deliveries",
    response_model=list[WebhookDeliveryOut],
    dependencies=[Depends(require_auth)],
)
def list_deliveries(
    webhook_id: int,
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(50, ge=1, le=200),
) -> list[WebhookDelivery]:
    _get_webhook_or_404(db, webhook_id)
    stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))
