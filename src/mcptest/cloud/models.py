"""SQLAlchemy ORM models for the cloud backend."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from mcptest.cloud.db import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TestRun(Base):
    """One stored mcptest Trace run.

    This is a denormalised representation: tool calls live inline as a JSON
    blob rather than in a separate table. That keeps the scaffold simple
    and makes typical dashboard queries (one row per case) trivial. A later
    phase can split tool_calls into its own table if aggregation needs it.
    """

    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    suite: Mapped[str | None] = mapped_column(String(256), nullable=True)
    case: Mapped[str | None] = mapped_column(String(256), nullable=True)
    input: Mapped[str] = mapped_column(String, default="")
    output: Mapped[str] = mapped_column(String, default="")
    exit_code: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    total_tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[bool] = mapped_column(Boolean, default=True)
    agent_error: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_calls: Mapped[list[Any]] = mapped_column(JSON, default=list)
    run_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metric_scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
