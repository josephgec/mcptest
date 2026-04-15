"""`/runs` endpoints — create, list, fetch mcptest test runs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mcptest.cloud.models import TestRun
from mcptest.cloud.schemas import TestRunCreate, TestRunOut


router = APIRouter(prefix="/runs", tags=["runs"])


def get_db() -> Session:  # pragma: no cover
    """Placeholder dependency — overridden by the app factory at startup."""
    raise NotImplementedError(
        "get_db must be overridden via app.dependency_overrides"
    )


@router.post(
    "",
    response_model=TestRunOut,
    status_code=status.HTTP_201_CREATED,
)
def create_run(
    payload: TestRunCreate,
    db: Annotated[Session, Depends(get_db)],
) -> TestRun:
    run = TestRun(**payload.model_dump())
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"trace_id {payload.trace_id!r} already exists",
        ) from exc
    db.refresh(run)
    return run


@router.get("", response_model=list[TestRunOut])
def list_runs(
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(50, ge=1, le=500),
    passed: bool | None = Query(None),
) -> list[TestRun]:
    stmt = select(TestRun).order_by(TestRun.created_at.desc()).limit(limit)
    if passed is not None:
        stmt = stmt.where(TestRun.passed == passed)
    return list(db.scalars(stmt))


@router.get("/{run_id}", response_model=TestRunOut)
def get_run(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> TestRun:
    run = db.get(TestRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no run with id {run_id}",
        )
    return run


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    run_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    run = db.get(TestRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no run with id {run_id}",
        )
    db.delete(run)
    db.commit()
