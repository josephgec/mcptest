"""Pytest config for the mcptest test suite.

Enables the `pytester` plugin so tests can spawn a nested pytest session
to exercise our own pytest11 plugin end-to-end.
"""

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Clear the in-process rate-limit log between every test.

    The rate limiter in ``mcptest.cloud.middleware`` uses a module-level
    dict keyed by client IP / API key.  FastAPI's ``TestClient`` sends all
    requests from the same IP ("testclient"), so without this reset the
    accumulated request count from one test bleeds into the next and causes
    spurious 429s.
    """
    try:
        from mcptest.cloud.middleware import _request_log

        _request_log.clear()
    except ImportError:
        pass  # cloud module not installed — nothing to reset
