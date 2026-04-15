"""SQLAlchemy base + engine/session factories for the cloud backend."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Declarative base shared by every SQLAlchemy model in the cloud."""


def make_engine(database_url: str, **kwargs: Any) -> Engine:
    """Build a SQLAlchemy engine.

    For file-based SQLite, sets ``check_same_thread=False`` so sessions can
    be used across threads (FastAPI TestClient uses threads).

    For in-memory SQLite (``sqlite:///:memory:``), additionally uses
    ``StaticPool`` so all connections share the same in-process database —
    without this, each new connection would get a fresh empty database and
    tables created by ``create_all`` would not be visible to later sessions.
    """
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    if database_url == "sqlite:///:memory:":
        kwargs.setdefault("poolclass", StaticPool)
    return create_engine(database_url, connect_args=connect_args, future=True, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def create_all(engine: Engine) -> None:
    """Create every registered table. Cloud scaffolding skips Alembic."""
    Base.metadata.create_all(bind=engine)
