"""SQLAlchemy ORM models for webhooks and delivery audit log."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mcptest.cloud.db import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Webhook(Base):
    """A registered webhook endpoint.

    Each webhook subscribes to one or more named events and optionally
    filters to a specific suite.  When an event fires, ``dispatch_event``
    delivers a signed JSON payload to ``url`` via HTTP POST.
    """

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # HMAC-SHA256 signing secret.  If None, no signature header is sent.
    secret: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # List of event names this webhook subscribes to, e.g. ["regression.detected"]
    events: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Only fire for this suite when set; None means fire for all suites.
    suite_filter: Mapped[str | None] = mapped_column(String(256), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )


class WebhookDelivery(Base):
    """Audit record for a single webhook delivery attempt."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    webhook_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Truncated response body (first 1 KB)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
