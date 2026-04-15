"""JSON Schema → example value generator.

Produces deterministic, schema-valid (or intentionally invalid) argument dicts
for use in generated test cases.  No external dependencies — pure Python.

Public API
----------
generate_valid(schema)               → dict of valid args (required fields only)
generate_type_error(schema, field)   → valid args with one field set to wrong type
generate_missing_required(schema, f) → valid args with one required field removed
generate_edge_cases(schema)          → list[EdgeCaseInput] of boundary-value inputs
generate_from_match(match)           → dict extracted from a fixture match condition
"""

from __future__ import annotations

from typing import Any, NamedTuple


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class EdgeCaseInput(NamedTuple):
    """One boundary-value input with a short descriptive label."""

    label: str  # e.g. "empty-title", "zero-count", "negative-id"
    args: dict[str, Any]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_WRONG_TYPE: dict[str, Any] = {
    "string": 12345,
    "integer": "not-a-number",
    "number": "not-a-number",
    "boolean": "not-a-boolean",
    "array": "not-an-array",
    "object": "not-an-object",
    "null": "not-null",
}


def _value_for_field(field_name: str, field_schema: dict) -> Any:
    """Return one representative valid value for a JSON Schema sub-schema."""
    # Enum: pick the first listed value.
    if "enum" in field_schema:
        return field_schema["enum"][0]

    # Explicit default: honour it.
    if "default" in field_schema:
        return field_schema["default"]

    typ = field_schema.get("type", "string")

    if typ == "string":
        minimum_len = field_schema.get("minLength", 0)
        maximum_len = field_schema.get("maxLength", None)
        base = f"example-{field_name}"
        # Satisfy minLength.
        if minimum_len > len(base):
            base = base + "x" * (minimum_len - len(base))
        # Satisfy maxLength.
        if maximum_len is not None and len(base) > maximum_len:
            base = base[:maximum_len]
        return base

    if typ == "integer":
        minimum = field_schema.get("minimum", None)
        return int(minimum) if minimum is not None and minimum >= 1 else 1

    if typ == "number":
        minimum = field_schema.get("minimum", None)
        return float(minimum) if minimum is not None and minimum >= 1.0 else 1.0

    if typ == "boolean":
        return True

    if typ == "array":
        items_schema = field_schema.get("items", {"type": "string"})
        return [_value_for_field(f"{field_name}_item", items_schema)]

    if typ == "object":
        nested_props = field_schema.get("properties", {})
        nested_required = field_schema.get("required", [])
        include = nested_required if nested_required else list(nested_props.keys())
        return {
            k: _value_for_field(k, nested_props[k])
            for k in include
            if k in nested_props
        }

    if typ == "null":
        return None

    # Unknown type — fall back to a string sentinel.
    return f"example-{field_name}"


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def generate_valid(schema: dict) -> dict:
    """Produce one valid input dict that satisfies *schema*.

    Only required fields are populated.  If the schema declares no ``required``
    list, all top-level properties are included instead.  Returns ``{}`` when
    the schema has neither.
    """
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    fields = required if required else list(properties.keys())
    result: dict[str, Any] = {}
    for name in fields:
        if name in properties:
            result[name] = _value_for_field(name, properties[name])
        else:
            # Field listed in required but not in properties — use a string.
            result[name] = f"example-{name}"
    return result


def generate_type_error(schema: dict, field: str) -> dict:
    """Return a valid input dict with *field* set to the wrong type.

    The base is a valid set of args; only the named field is replaced with a
    value of the opposite type so the agent or server should reject it.
    """
    args = generate_valid(schema)
    # Make sure the field is present even if it was optional.
    properties: dict[str, Any] = schema.get("properties", {})
    field_schema = properties.get(field, {})
    expected_type = field_schema.get("type", "string")
    args[field] = _WRONG_TYPE.get(expected_type, 12345)
    return args


def generate_missing_required(schema: dict, field: str) -> dict:
    """Return a valid input dict with the required *field* removed."""
    args = generate_valid(schema)
    args.pop(field, None)
    return args


def generate_edge_cases(schema: dict) -> list[EdgeCaseInput]:
    """Produce boundary-value inputs for each required field.

    Per-field variants generated:

    * ``string``   → empty string ``""`` and a 100-character long string
    * ``integer``  → ``0`` (if no minimum > 0) and ``-1`` (if no minimum >= 0)
    * ``number``   → ``0.0`` and ``-1.0`` under the same conditions
    * ``array``    → empty list ``[]``

    Each returned ``EdgeCaseInput`` carries a *label* (e.g. ``"empty-title"``)
    that the engine embeds in the generated test case name.
    """
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])
    base = generate_valid(schema)

    cases: list[EdgeCaseInput] = []

    # Iterate over *required* fields (or all fields when none are required).
    target_fields = required if required else list(properties.keys())

    for field_name in target_fields:
        field_schema = properties.get(field_name, {})
        typ = field_schema.get("type", "string")

        if typ == "string":
            cases.append(EdgeCaseInput(
                label=f"empty-{field_name}",
                args={**base, field_name: ""},
            ))
            max_len = field_schema.get("maxLength", None)
            long_val = "x" * (max_len if max_len is not None else 100)
            cases.append(EdgeCaseInput(
                label=f"long-{field_name}",
                args={**base, field_name: long_val},
            ))

        elif typ in ("integer", "number"):
            minimum = field_schema.get("minimum", None)
            zero = 0 if typ == "integer" else 0.0
            neg = -1 if typ == "integer" else -1.0
            if minimum is None or minimum <= 0:
                cases.append(EdgeCaseInput(
                    label=f"zero-{field_name}",
                    args={**base, field_name: zero},
                ))
            if minimum is None or minimum < 0:
                cases.append(EdgeCaseInput(
                    label=f"negative-{field_name}",
                    args={**base, field_name: neg},
                ))

        elif typ == "array":
            cases.append(EdgeCaseInput(
                label=f"empty-{field_name}",
                args={**base, field_name: []},
            ))

    return cases


def generate_from_match(match: dict) -> dict:
    """Return a copy of a fixture ``match`` dict as known-good arg values."""
    return dict(match)
