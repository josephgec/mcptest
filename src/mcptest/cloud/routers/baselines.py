"""`/runs/{id}/promote`, `/baselines`, and `/runs/{id}/check` endpoints.

Implements the regression guard loop:
  1. Promote a run as the "gold standard" baseline for its suite.
  2. Push a new run, call ``POST /runs/{id}/check`` to auto-compare it
     against the latest baseline — no need to specify base/head IDs manually.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from mcptest.cloud.models import TestRun
from mcptest.cloud.routers.runs import get_db
from mcptest.cloud.schemas import (
    AutoCompareOut,
    BaselinePromoteOut,
    ComparisonDelta,
    TestRunOut,
)
from mcptest.metrics.base import METRICS

router = APIRouter(tags=["baselines"])

_DEFAULT_THRESHOLD = 0.1


# ---------------------------------------------------------------------------
# Promote / demote
# ---------------------------------------------------------------------------


@router.post(
    "/runs/{run_id}/promote",
    response_model=BaselinePromoteOut,
    status_code=status.HTTP_200_OK,
)
def promote_baseline(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> BaselinePromoteOut:
    """Mark *run_id* as the baseline for its suite.

    Atomically clears ``is_baseline`` on the previous baseline for the same
    suite so there is always at most one baseline per suite.
    """
    run = db.get(TestRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no run with id {run_id}",
        )

    # Demote any existing baseline for the same suite.
    existing_stmt = select(TestRun).where(
        TestRun.suite == run.suite,
        TestRun.is_baseline.is_(True),
        TestRun.id != run_id,
    )
    for old in db.scalars(existing_stmt):
        old.is_baseline = False

    run.is_baseline = True
    db.commit()
    db.refresh(run)
    return BaselinePromoteOut(
        id=run.id,
        suite=run.suite,
        is_baseline=True,
        message=f"run {run_id} is now the baseline for suite {run.suite!r}",
    )


@router.delete(
    "/runs/{run_id}/promote",
    response_model=BaselinePromoteOut,
    status_code=status.HTTP_200_OK,
)
def demote_baseline(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> BaselinePromoteOut:
    """Remove the baseline flag from *run_id*."""
    run = db.get(TestRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no run with id {run_id}",
        )

    run.is_baseline = False
    db.commit()
    db.refresh(run)
    return BaselinePromoteOut(
        id=run.id,
        suite=run.suite,
        is_baseline=False,
        message=f"run {run_id} is no longer the baseline",
    )


# ---------------------------------------------------------------------------
# List baselines
# ---------------------------------------------------------------------------


@router.get("/baselines", response_model=list[TestRunOut])
def list_baselines(
    db: Annotated[Session, Depends(get_db)],
    suite: str | None = Query(None),
) -> list[TestRun]:
    """Return all runs marked as baselines, optionally filtered by *suite*."""
    stmt = (
        select(TestRun)
        .where(TestRun.is_baseline.is_(True))
        .order_by(TestRun.created_at.desc())
    )
    if suite is not None:
        stmt = stmt.where(TestRun.suite == suite)
    return list(db.scalars(stmt))


# ---------------------------------------------------------------------------
# Auto-compare (regression check)
# ---------------------------------------------------------------------------


@router.post("/runs/{run_id}/check", response_model=AutoCompareOut)
def check_run(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
    thresholds: dict[str, float] | None = None,
) -> AutoCompareOut:
    """Auto-compare *run_id* against the latest baseline for the same suite.

    If no baseline exists, returns ``status="no_baseline"`` (HTTP 200) rather
    than 404 — the caller should treat this as informational, not an error.

    Threshold logic mirrors ``POST /compare``: a metric regresses when
    ``head_score - base_score < -threshold``. The default threshold is 0.1.
    """
    head_run = db.get(TestRun, run_id)
    if head_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no run with id {run_id}",
        )

    # Find the latest baseline for the same suite.
    baseline_stmt = (
        select(TestRun)
        .where(
            TestRun.suite == head_run.suite,
            TestRun.is_baseline.is_(True),
            TestRun.id != run_id,
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    )
    base_run = db.scalars(baseline_stmt).first()

    if base_run is None:
        return AutoCompareOut(
            base_id=None,
            head_id=run_id,
            deltas=[],
            overall_passed=True,
            regression_count=0,
            baseline_id=None,
            baseline_branch=None,
            status="no_baseline",
        )

    thresholds = thresholds or {}
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
    check_status = "pass" if overall_passed else "fail"

    return AutoCompareOut(
        base_id=base_run.id,
        head_id=run_id,
        deltas=deltas,
        overall_passed=overall_passed,
        regression_count=sum(1 for d in deltas if d.regressed),
        baseline_id=base_run.id,
        baseline_branch=base_run.branch,
        status=check_status,
    )
