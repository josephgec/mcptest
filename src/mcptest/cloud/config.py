"""Configuration for the cloud backend, driven by env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    """Runtime configuration for the cloud service.

    Kept intentionally minimal — no Pydantic settings dependency, no .env
    loader. Scaffolding only. Fields map to `MCPTEST_*` environment variables.
    """

    database_url: str = "sqlite:///./mcptest_cloud.db"
    title: str = "mcptest cloud"
    version: str = "0.1.0"
    debug: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get(
                "MCPTEST_DATABASE_URL", "sqlite:///./mcptest_cloud.db"
            ),
            title=os.environ.get("MCPTEST_CLOUD_TITLE", "mcptest cloud"),
            version=os.environ.get("MCPTEST_CLOUD_VERSION", "0.1.0"),
            debug=os.environ.get("MCPTEST_CLOUD_DEBUG", "").lower()
            in ("1", "true", "yes"),
        )
