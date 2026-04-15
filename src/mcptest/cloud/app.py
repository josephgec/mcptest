"""FastAPI application factory for the cloud backend."""

from __future__ import annotations

import os
from typing import Iterator

from fastapi import FastAPI
from fastapi.middleware import Middleware
from sqlalchemy.orm import Session

from mcptest.cloud.config import Settings
from mcptest.cloud.db import create_all, make_engine, make_session_factory
from mcptest.cloud.dashboard import create_dashboard_router
from mcptest.cloud.dashboard import routes as dashboard_routes
from mcptest.cloud.middleware import add_cors_middleware, rate_limit_middleware
from mcptest.cloud.routers import baselines, compare, health, metrics, runs


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a FastAPI app wired up to the configured database.

    The session dependency is overridden here (rather than hand-wired per
    request) so tests can construct an app against an in-memory SQLite
    database without monkeypatching globals.

    Auth / middleware wiring
    ------------------------
    * CORS — ``MCPTEST_CORS_ORIGINS`` (default ``*``)
    * Rate limiting — ``MCPTEST_RATE_LIMIT`` req/min per key/IP (default 60)
    * API-key auth — ``MCPTEST_API_KEYS`` comma-separated; enforced on write
      endpoints always, and on read endpoints when
      ``MCPTEST_AUTH_REQUIRED=true``.
    """
    settings = settings or Settings.from_env()

    # Inject auth settings into the environment so auth.py can read them.
    # This approach keeps auth.py stateless (reads env at call time) while
    # letting callers pass Settings objects in tests without monkeypatching.
    _apply_auth_env(settings)

    engine = make_engine(settings.database_url)
    SessionLocal = make_session_factory(engine)
    create_all(engine)

    app = FastAPI(
        title=settings.title,
        version=settings.version,
        debug=settings.debug,
    )

    # --- Middleware (order matters: outermost first) -----------------------
    add_cors_middleware(app, origins=settings.cors_origins)
    app.middleware("http")(rate_limit_middleware)

    # --- Session dependency override --------------------------------------
    def get_db() -> Iterator[Session]:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[runs.get_db] = get_db
    app.dependency_overrides[dashboard_routes.get_db] = get_db

    # --- Routers -----------------------------------------------------------
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(compare.router)
    app.include_router(baselines.router)
    app.include_router(metrics.router)
    app.include_router(create_dashboard_router())

    # Attach the db engine to app state so /health/ready can probe it.
    app.state.db_engine = engine

    return app


def _apply_auth_env(settings: Settings) -> None:
    """Push Settings auth fields into the process environment.

    ``auth.py`` reads env vars at dependency-injection time so it stays
    stateless.  Calling this once at app-factory time ensures the right
    keys are in place for the lifetime of the app.

    In tests, each test creates a fresh app with explicit Settings, so this
    runs once per test and effectively scopes auth to that test's settings.
    """
    if settings.api_keys:
        os.environ["MCPTEST_API_KEYS"] = ",".join(sorted(settings.api_keys))
    else:
        os.environ.pop("MCPTEST_API_KEYS", None)

    if settings.auth_required:
        os.environ["MCPTEST_AUTH_REQUIRED"] = "true"
    else:
        os.environ.pop("MCPTEST_AUTH_REQUIRED", None)

    os.environ["MCPTEST_RATE_LIMIT"] = str(settings.rate_limit)
    os.environ["MCPTEST_CORS_ORIGINS"] = ",".join(settings.cors_origins)
