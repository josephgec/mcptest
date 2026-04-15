"""Liveness/readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from mcptest.cloud.schemas import HealthStatus


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus()
