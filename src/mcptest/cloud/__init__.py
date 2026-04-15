"""mcptest cloud backend — FastAPI + SQLAlchemy scaffolding.

This package provides the smallest possible backend capable of receiving and
storing mcptest trace runs, to be fleshed out into a full hosted dashboard
in a later phase. It deliberately ships without authentication, billing, or
a frontend — those are out of scope for the scaffold.
"""

from __future__ import annotations

from mcptest.cloud.app import create_app
from mcptest.cloud.config import Settings
from mcptest.cloud.db import Base, make_engine, make_session_factory
from mcptest.cloud.models import TestRun
from mcptest.cloud.schemas import HealthStatus, TestRunCreate, TestRunOut

__all__ = [
    "Base",
    "HealthStatus",
    "Settings",
    "TestRun",
    "TestRunCreate",
    "TestRunOut",
    "create_app",
    "make_engine",
    "make_session_factory",
]
