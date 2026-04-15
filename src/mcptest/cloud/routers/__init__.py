"""FastAPI routers for the cloud backend."""

from __future__ import annotations

from mcptest.cloud.routers.compare import router as compare_router
from mcptest.cloud.routers.health import router as health_router
from mcptest.cloud.routers.runs import router as runs_router

__all__ = ["compare_router", "health_router", "runs_router"]
