"""Plugin discovery and loading for mcptest.

Plugins extend the built-in registries (ASSERTIONS, METRICS, EXPORTERS) by
importing Python code that calls the registration decorators as a side-effect.

Three sources are checked, in order:

1. ``confmcptest.py`` files found in or above the configured test directories.
2. Entries in the ``plugins:`` list of ``mcptest.yaml`` — either dotted
   module names (``my_company.mcptest_ext``) or relative/absolute file paths.
3. Installed packages that advertise themselves via ``setuptools`` entry
   points under ``mcptest.assertions``, ``mcptest.metrics``, or
   ``mcptest.exporters``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import sys
from pathlib import Path

from mcptest.config import McpTestConfig

logger = logging.getLogger(__name__)

_ENTRY_POINT_GROUPS = (
    "mcptest.assertions",
    "mcptest.metrics",
    "mcptest.exporters",
)

_CONFMCPTEST_FILENAME = "confmcptest.py"


def load_plugins(config: McpTestConfig) -> list[str]:
    """Discover and load all plugins defined in *config*.

    Returns a list of loaded module/file specifiers (used by ``mcptest
    config`` for display).  Errors from individual plugins are logged as
    warnings and do not abort the process.
    """
    loaded: list[str] = []

    # 1. confmcptest.py auto-discovery
    search_dirs = _resolve_search_dirs(config)
    for path in discover_confmcptest(search_dirs):
        if _load_file_module(path):
            loaded.append(str(path))

    # 2. Explicit plugins list from config
    for specifier in config.plugins:
        if _load_module(specifier):
            loaded.append(specifier)

    # 3. Entry points from installed packages
    loaded.extend(discover_entry_points())

    return loaded


def _resolve_search_dirs(config: McpTestConfig) -> list[Path]:
    """Return the directories to search for ``confmcptest.py`` files."""
    cwd = Path.cwd()
    dirs: list[Path] = []
    if config.test_paths:
        for p in config.test_paths:
            resolved = (cwd / p).resolve()
            if resolved.is_dir():
                dirs.append(resolved)
    if not dirs:
        default = cwd / "tests"
        if default.is_dir():
            dirs.append(default)
        else:
            dirs.append(cwd)
    return dirs


def discover_confmcptest(search_dirs: list[Path]) -> list[Path]:
    """Find ``confmcptest.py`` files in *search_dirs* and their parents.

    Walks each directory upward, collecting any ``confmcptest.py`` found,
    stopping at the filesystem root.  Returns unique paths in outermost-first
    order so that parent configs are loaded before child configs.
    """
    found: list[Path] = []
    seen: set[Path] = set()

    for start in search_dirs:
        directory = start.resolve()
        chain: list[Path] = []
        while True:
            candidate = directory / _CONFMCPTEST_FILENAME
            if candidate not in seen and candidate.is_file():
                chain.append(candidate)
                seen.add(candidate)
            parent = directory.parent
            if parent == directory:
                break
            directory = parent
        # Outermost first (reversed so root is first, test dir is last)
        found.extend(reversed(chain))

    return found


def _load_module(specifier: str) -> bool:
    """Load a plugin by dotted module name or file path.

    Returns ``True`` on success, ``False`` on failure (after logging a
    warning).
    """
    # Distinguish file paths from dotted module names.
    if specifier.endswith(".py") or "/" in specifier or "\\" in specifier:
        path = Path(specifier)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        return _load_file_module(path)
    return _load_dotted_module(specifier)


def _load_file_module(path: Path) -> bool:
    """Import a plugin from a ``.py`` file.

    Assigns a unique synthetic module name based on the absolute path to
    avoid collisions when multiple ``confmcptest.py`` files exist at
    different directory levels.
    """
    if not path.is_file():
        logger.warning("Plugin file not found: %s", path)
        return False
    module_name = f"_mcptest_plugin_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        logger.warning("Could not create module spec for plugin: %s", path)
        return False
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        logger.warning("Error loading plugin %s: %s", path, exc)
        del sys.modules[module_name]
        return False
    return True


def _load_dotted_module(name: str) -> bool:
    """Import a plugin by dotted module name (e.g. ``my_company.mcptest_ext``)."""
    try:
        importlib.import_module(name)
        return True
    except ImportError as exc:
        logger.warning("Could not import plugin %r: %s", name, exc)
        return False


def discover_entry_points() -> list[str]:
    """Load all plugins registered via ``setuptools`` entry points.

    Third-party packages register under one of:

    - ``mcptest.assertions``
    - ``mcptest.metrics``
    - ``mcptest.exporters``

    Each entry point value should be a dotted import path to a module that
    registers its extensions when imported.  Returns names of successfully
    loaded entry points.
    """
    loaded: list[str] = []
    for group in _ENTRY_POINT_GROUPS:
        try:
            eps = importlib.metadata.entry_points(group=group)
        except Exception:
            continue
        for ep in eps:
            try:
                ep.load()
                loaded.append(f"{group}:{ep.name}")
            except Exception as exc:
                logger.warning(
                    "Failed to load entry point %s:%s — %s", group, ep.name, exc
                )
    return loaded
