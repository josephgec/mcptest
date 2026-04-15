"""`GET /metrics/history` endpoint — time-series metric trend tracking."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from mcptest.cloud.models import TestRun
from mcptest.cloud.routers.runs import get_db
from mcptest.cloud.schemas import MetricHistoryOut, MetricHistoryPoint

router = APIRouter(tags=["metrics"])

_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


@router.get("/metrics/history", response_model=MetricHistoryOut)
def get_metric_history(
    db: Annotated[Session, Depends(get_db)],
    suite: str | None = Query(None),
    branch: str | None = Query(None),
    metric: str | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> MetricHistoryOut:
    """Return time-series metric data for trend tracking.

    Results are ordered newest-first (``created_at DESC``).  When *metric* is
    supplied, each point's ``metric_scores`` dict is filtered to contain only
    that single metric — convenient for charting one metric at a time.
    """
    stmt = (
        select(TestRun)
        .order_by(TestRun.created_at.desc())
        .limit(limit)
    )
    if suite is not None:
        stmt = stmt.where(TestRun.suite == suite)
    if branch is not None:
        stmt = stmt.where(TestRun.branch == branch)

    runs = list(db.scalars(stmt))

    points: list[MetricHistoryPoint] = []
    for run in runs:
        scores: dict[str, float] = run.metric_scores or {}
        if metric is not None:
            scores = {metric: scores[metric]} if metric in scores else {}
        points.append(
            MetricHistoryPoint(
                run_id=run.id,
                created_at=run.created_at,
                branch=run.branch,
                metric_scores=scores,
            )
        )

    return MetricHistoryOut(
        points=points,
        suite=suite,
        branch=branch,
        metric=metric,
    )
