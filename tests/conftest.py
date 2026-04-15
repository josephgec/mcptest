"""Pytest config for the mcptest test suite.

Enables the `pytester` plugin so tests can spawn a nested pytest session
to exercise our own pytest11 plugin end-to-end.
"""

pytest_plugins = ["pytester"]
