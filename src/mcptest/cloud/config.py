"""Configuration for the cloud backend, driven by env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """Runtime configuration for the cloud service.

    Fields map to ``MCPTEST_*`` environment variables.

    Auth / security
    ---------------
    ``api_keys``        — set of valid API keys (from ``MCPTEST_API_KEYS``,
                          comma-separated).  Empty set disables key checks.
    ``auth_required``   — when ``True`` (``MCPTEST_AUTH_REQUIRED=true``),
                          even read-only endpoints require a valid key.
                          Defaults to ``False`` for local dev.
    ``cors_origins``    — list of allowed CORS origins
                          (``MCPTEST_CORS_ORIGINS``, comma-separated).
                          Defaults to ``["*"]``.
    ``rate_limit``      — max requests per 60-second window per API key / IP
                          (``MCPTEST_RATE_LIMIT``).  Defaults to 60.
    """

    database_url: str = "sqlite:///./mcptest_cloud.db"
    title: str = "mcptest cloud"
    version: str = "0.1.0"
    debug: bool = False
    # Auth
    api_keys: frozenset[str] = field(default_factory=frozenset)
    auth_required: bool = False
    # CORS
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    # Rate limiting
    rate_limit: int = 60

    @classmethod
    def from_env(cls) -> Settings:
        raw_keys = os.environ.get("MCPTEST_API_KEYS", "")
        api_keys: frozenset[str] = frozenset(
            k.strip() for k in raw_keys.split(",") if k.strip()
        )

        raw_origins = os.environ.get("MCPTEST_CORS_ORIGINS", "")
        cors_origins = (
            [o.strip() for o in raw_origins.split(",") if o.strip()] or ["*"]
        )

        try:
            rate_limit = max(1, int(os.environ.get("MCPTEST_RATE_LIMIT", "60")))
        except ValueError:
            rate_limit = 60

        return cls(
            database_url=os.environ.get(
                "MCPTEST_DATABASE_URL", "sqlite:///./mcptest_cloud.db"
            ),
            title=os.environ.get("MCPTEST_CLOUD_TITLE", "mcptest cloud"),
            version=os.environ.get("MCPTEST_CLOUD_VERSION", "0.1.0"),
            debug=os.environ.get("MCPTEST_CLOUD_DEBUG", "").lower()
            in ("1", "true", "yes"),
            api_keys=api_keys,
            auth_required=os.environ.get("MCPTEST_AUTH_REQUIRED", "").lower()
            in ("1", "true", "yes"),
            cors_origins=cors_origins,
            rate_limit=rate_limit,
        )
