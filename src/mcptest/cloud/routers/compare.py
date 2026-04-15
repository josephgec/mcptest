"""`POST /compare` endpoint — score-based metric regression detection.

Compares two stored test runs by their saved ``metric_scores`` dicts rather
than by re-running the traces. This makes comparisons instant even for runs
stored weeks apart and keeps the endpoint dependency-free (no agent process
needs to be invoked).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from mcptest.cloud.models import TestRun
from mcptest.cloud.routers.runs import get_db
from mcptest.cloud.schemas import CompareRequest, ComparisonDelta, ComparisonOut
from mcptest.metrics.base import METRICS

router = APIRouter(tags=["compare"])

_DEFAULT_THRESHOLD = 0.1


@router.post("/compare", response_model=ComparisonOut)
def compare_runs(
    payload: CompareRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ComparisonOut:
    """Compare two runs by their stored metric_scores.

    Looks up ``base_id`` and ``head_id`` in the database, then computes
    per-metric deltas from the stored ``metric_scores`` JSON column. Returns a
    ``ComparisonOut`` with regression flags for each metric that dropped
    beyond the given (or default) threshold.
    """
    base_run = db.get(TestRun, payload.base_id)
    if base_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no run with id {payload.base_id}",
        )
    head_run = db.get(TestRun, payload.head_id)
    if head_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no run with id {payload.head_id}",
        )

    thresholds: dict[str, float] = payload.thresholds or {}
    base_scores: dict[str, float] = base_run.metric_scores or {}
    head_scores: dict[str, float] = head_run.metric_scores or {}

    all_names = sorted(set(base_scores) | set(head_scores))

    deltas: list[ComparisonDelta] = []
    for name in all_names:
        if name not in base_scores or name not in head_scores:
            continue
        b = base_scores[name]
        h = head_scores[name]
        threshold = thresholds.get(name, _DEFAULT_THRESHOLD)
        diff = h - b
        # Resolve a human-readable label from the metric registry when possible.
        metric_cls = METRICS.get(name)
        label = metric_cls.label if metric_cls else name
        deltas.append(
            ComparisonDelta(
                name=name,
                label=label,
                base_score=b,
                head_score=h,
                delta=diff,
                regressed=diff < -threshold,
            )
        )

    overall_passed = not any(d.regressed for d in deltas)
    return ComparisonOut(
        base_id=payload.base_id,
        head_id=payload.head_id,
        deltas=deltas,
        overall_passed=overall_passed,
        regression_count=sum(1 for d in deltas if d.regressed),
    )
