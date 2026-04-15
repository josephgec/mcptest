"""Response matching logic for the mock MCP server.

Given a tool's configured list of `Response` entries and the arguments from an
incoming tool call, pick which response should be returned. Matching proceeds
top-to-bottom; the first entry whose `match`/`match_regex` conditions are all
satisfied wins. An entry with `default: true` acts as a fallback and is only
considered after no earlier non-default entry matched.
"""

from __future__ import annotations

import re
from typing import Any

from mcptest.fixtures.models import Response


class NoMatchError(Exception):
    """Raised when no response rule matches the incoming arguments."""


def _value_matches(expected: Any, actual: Any) -> bool:
    """Whether a single `match:` entry's expected value matches the actual arg.

    - Scalars compare by equality.
    - Nested dicts compare structurally (all keys in `expected` must match).
    - Lists compare element-wise at their shared prefix.
    """
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            k in actual and _value_matches(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        if len(actual) < len(expected):
            return False
        return all(_value_matches(e, a) for e, a in zip(expected, actual))
    return expected == actual


def _exact_matches(match: dict[str, Any], arguments: dict[str, Any]) -> bool:
    return all(k in arguments and _value_matches(v, arguments[k]) for k, v in match.items())


def _regex_matches(match_regex: dict[str, str], arguments: dict[str, Any]) -> bool:
    for key, pattern in match_regex.items():
        if key not in arguments:
            return False
        value = arguments[key]
        if not isinstance(value, str):
            value = str(value)
        try:
            if re.search(pattern, value) is None:
                return False
        except re.error:
            return False
    return True


def match_response(
    responses: list[Response], arguments: dict[str, Any]
) -> Response:
    """Pick the first matching response, or the default, or raise."""
    default: Response | None = None
    for response in responses:
        if response.default:
            # Remember the first default but keep scanning for explicit matches.
            if default is None:
                default = response
            continue

        exact_ok = response.match is None or _exact_matches(response.match, arguments)
        regex_ok = response.match_regex is None or _regex_matches(
            response.match_regex, arguments
        )
        if exact_ok and regex_ok:
            return response

    if default is not None:
        return default

    raise NoMatchError(
        f"no response rule matched arguments {arguments!r}"
    )
