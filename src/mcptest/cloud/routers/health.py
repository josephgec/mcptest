"""Liveness and readiness endpoints.

* ``GET /health``       — liveness probe; no DB check, always fast.
* ``GET /health/ready`` — readiness probe; verifies database connectivity.
  Returns HTTP 200 with ``status="ready"`` when the DB responds, or HTTP 503
  with ``status="unavailable"`` when it does not.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from mcptest.cloud.schemas import HealthReadyStatus, HealthStatus


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    """Liveness probe — returns immediately without touching the database."""
    return HealthStatus()


@router.get(
    "/health/ready",
    response_model=HealthReadyStatus,
    responses={503: {"model": HealthReadyStatus}},
)
def health_ready(request: Request) -> JSONResponse:
    """Readiness probe — verifies the database is reachable.

    The engine is attached to ``app.state.db_engine`` by the app factory.
    If the app was not created via ``create_app`` (e.g. unit tests that build
    the router directly) the check is skipped and the endpoint reports ready.
    """
    engine = getattr(request.app.state, "db_engine", None)
    if engine is None:
        body = HealthReadyStatus(status="ready", db="ok")
        return JSONResponse(content=body.model_dump(), status_code=status.HTTP_200_OK)

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        body = HealthReadyStatus(status="ready", db="ok")
        return JSONResponse(content=body.model_dump(), status_code=status.HTTP_200_OK)
    except Exception as exc:  # noqa: BLE001
        body = HealthReadyStatus(status="unavailable", db=f"error: {exc}")
        return JSONResponse(
            content=body.model_dump(), status_code=status.HTTP_503_SERVICE_UNAVAILABLE
        )
