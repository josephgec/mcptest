"""FastAPI application factory for the cloud backend."""

from __future__ import annotations

from typing import Iterator

from fastapi import FastAPI
from sqlalchemy.orm import Session

from mcptest.cloud.config import Settings
from mcptest.cloud.db import create_all, make_engine, make_session_factory
from mcptest.cloud.routers import compare, health, runs


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a FastAPI app wired up to the configured database.

    The session dependency is overridden here (rather than hand-wired per
    request) so tests can construct an app against an in-memory SQLite
    database without monkeypatching globals.
    """
    settings = settings or Settings.from_env()

    engine = make_engine(settings.database_url)
    SessionLocal = make_session_factory(engine)
    create_all(engine)

    app = FastAPI(
        title=settings.title,
        version=settings.version,
        debug=settings.debug,
    )

    def get_db() -> Iterator[Session]:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[runs.get_db] = get_db

    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(compare.router)

    return app
