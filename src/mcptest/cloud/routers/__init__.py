"""FastAPI routers for the cloud backend."""

from __future__ import annotations

from mcptest.cloud.routers.baselines import router as baselines_router
from mcptest.cloud.routers.compare import router as compare_router
from mcptest.cloud.routers.health import router as health_router
from mcptest.cloud.routers.metrics import router as metrics_router
from mcptest.cloud.routers.runs import router as runs_router

__all__ = [
    "baselines_router",
    "compare_router",
    "health_router",
    "metrics_router",
    "runs_router",
]
