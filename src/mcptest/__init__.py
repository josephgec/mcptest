"""mcptest — pytest for MCP agents.

Every public name is re-exported lazily via `__getattr__`. This is deliberate:
pytest's `pytest11` entry point causes `mcptest.pytest_plugin` (and therefore
`mcptest` itself) to load before `pytest-cov` starts instrumenting. Any
eager `from mcptest.X import Y` at module level would lock those submodules
into the Python module cache uninstrumented, dropping the reported coverage
by ~30%. Lazy attribute access keeps those imports out of the pre-coverage
phase so everything gets measured the first time a test touches it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "0.1.0"

if TYPE_CHECKING:  # re-exports visible to static type checkers only
    from mcptest.fixtures import (  # noqa: F401
        ErrorSpec,
        Fixture,
        FixtureLoadError,
        Response,
        ResourceSpec,
        ServerSpec,
        ToolSpec,
        load_fixture,
        load_fixtures,
    )
    from mcptest.pytest_plugin import mock  # noqa: F401


_LAZY_REEXPORTS = {
    "ErrorSpec": ("mcptest.fixtures", "ErrorSpec"),
    "Fixture": ("mcptest.fixtures", "Fixture"),
    "FixtureLoadError": ("mcptest.fixtures", "FixtureLoadError"),
    "Response": ("mcptest.fixtures", "Response"),
    "ResourceSpec": ("mcptest.fixtures", "ResourceSpec"),
    "ServerSpec": ("mcptest.fixtures", "ServerSpec"),
    "ToolSpec": ("mcptest.fixtures", "ToolSpec"),
    "load_fixture": ("mcptest.fixtures", "load_fixture"),
    "load_fixtures": ("mcptest.fixtures", "load_fixtures"),
    "mock": ("mcptest.pytest_plugin", "mock"),
}


def __getattr__(name: str):
    spec = _LAZY_REEXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module 'mcptest' has no attribute {name!r}")
    module_name, attr = spec
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, attr)
    globals()[name] = value
    return value


__all__ = [
    "__version__",
    *sorted(_LAZY_REEXPORTS),
]
