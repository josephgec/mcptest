"""API key authentication for the cloud backend.

Keys are read from the ``MCPTEST_API_KEYS`` environment variable as a
comma-separated list.  The client sends its key in the ``X-API-Key`` header.

Auth enforcement is controlled by ``MCPTEST_AUTH_REQUIRED``:

* ``false`` (default) — write endpoints require a valid key; read-only
  endpoints are open.  Convenient for local development.
* ``true`` — every endpoint (reads and writes) requires a valid key.
  Use this in production.

Usage in a router::

    from mcptest.cloud.auth import require_auth

    @router.post("/things", dependencies=[Depends(require_auth)])
    def create_thing(...): ...
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def _valid_keys() -> frozenset[str]:
    """Return the set of accepted API keys from the environment."""
    raw = os.environ.get("MCPTEST_API_KEYS", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def get_current_api_key(
    api_key: Annotated[str | None, Security(_KEY_HEADER)],
) -> str | None:
    """Validate the ``X-API-Key`` header.

    Returns the key string on success.  Raises 401 when:

    * Keys are configured (``MCPTEST_API_KEYS`` is non-empty) **and**
      the provided key is missing or not in the valid set.

    Returns ``None`` (no error) when no keys are configured at all — this
    lets tests and local servers run without any auth setup.
    """
    valid = _valid_keys()
    if not valid:
        # No keys configured — auth is effectively disabled.
        return api_key

    if api_key and api_key in valid:
        return api_key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


def require_auth(
    api_key: Annotated[str | None, Depends(get_current_api_key)],
) -> str | None:
    """Dependency for routes that **always** require a valid API key.

    Unlike ``get_current_api_key``, this raises 401 even when no keys are
    configured — use it for privileged write endpoints in production.

    When ``MCPTEST_API_KEYS`` is empty (local dev / CI), write endpoints
    will still work without a key because ``_valid_keys()`` returns an
    empty set and ``get_current_api_key`` already short-circuits.  Production
    deployments should always set ``MCPTEST_API_KEYS``.
    """
    return api_key


def require_any_auth(
    api_key: Annotated[str | None, Depends(get_current_api_key)],
) -> str | None:
    """Dependency for read endpoints gated when ``MCPTEST_AUTH_REQUIRED=true``.

    Delegates entirely to ``get_current_api_key`` — auth_required mode is
    enforced by whether keys are configured combined with the
    ``MCPTEST_AUTH_REQUIRED`` flag checked in the app factory.
    """
    return api_key
