"""Dashboard package for the mcptest cloud web UI."""

from __future__ import annotations

from mcptest.cloud.dashboard.routes import create_dashboard_router, get_db

__all__ = ["create_dashboard_router", "get_db"]
