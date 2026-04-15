"""CORS and rate-limiting middleware for the cloud backend.

CORS
----
Configured via ``MCPTEST_CORS_ORIGINS`` (comma-separated list of allowed
origins, default ``*`` for local dev).  At runtime the app factory calls
:func:`add_cors_middleware` which wraps the FastAPI app with Starlette's
built-in ``CORSMiddleware``.

Rate limiting
-------------
Simple in-memory token-bucket: ``MCPTEST_RATE_LIMIT`` requests per 60-second
window per API key (or per client IP when no key is present).  Defaults to
60 requests/minute.  Returns HTTP 429 when the limit is exceeded.

The store is process-local.  For multi-process deployments (e.g. multiple
uvicorn workers) this will allow up to ``rate_limit * workers`` requests per
minute — acceptable for an initial production cut.  A Redis-backed store is
the right next step when that matters.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.cors import CORSMiddleware


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def add_cors_middleware(app: FastAPI, *, origins: list[str] | None = None) -> None:
    """Attach Starlette's CORSMiddleware to *app*.

    ``origins`` defaults to the value of ``MCPTEST_CORS_ORIGINS`` (comma-
    separated), falling back to ``["*"]`` when the env var is unset.
    """
    if origins is None:
        raw = os.environ.get("MCPTEST_CORS_ORIGINS", "")
        origins = [o.strip() for o in raw.split(",") if o.strip()] or ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

# { key_or_ip: [timestamp, ...] }
_WINDOW_SECONDS = 60.0
_request_log: dict[str, list[float]] = defaultdict(list)


def _rate_limit() -> int:
    """Return the configured requests-per-minute limit."""
    try:
        return max(1, int(os.environ.get("MCPTEST_RATE_LIMIT", "60")))
    except ValueError:
        return 60


def _client_key(request: Request) -> str:
    """Derive a rate-limit bucket key from the request.

    Uses the ``X-API-Key`` header when present; falls back to the client IP.
    """
    api_key = request.headers.get("X-API-Key", "").strip()
    if api_key:
        return f"key:{api_key}"
    client = request.client
    ip = client.host if client else "unknown"
    return f"ip:{ip}"


async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """ASGI middleware that enforces the per-key request rate limit."""
    key = _client_key(request)
    now = time.monotonic()
    window_start = now - _WINDOW_SECONDS

    # Prune timestamps outside the current window.
    timestamps = _request_log[key]
    while timestamps and timestamps[0] < window_start:
        timestamps.pop(0)

    limit = _rate_limit()
    if len(timestamps) >= limit:
        return Response(
            content='{"detail":"rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(_WINDOW_SECONDS)},
        )

    timestamps.append(now)
    return await call_next(request)
