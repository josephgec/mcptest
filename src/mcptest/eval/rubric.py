"""Rubric data model for semantic evaluation.

A :class:`Rubric` is a named collection of :class:`Criterion` objects, each of
which defines one dimension of evaluation (e.g. correctness, completeness,
format).  Rubrics can be loaded from a standalone YAML file or inlined inside
a test spec's ``eval:`` section.

Example standalone YAML::

    rubric:
      name: booking-quality
      criteria:
        - name: correctness
          weight: 0.5
          method: keywords
          expected: [confirmed, booking_id, receipt]
          threshold: 0.6
        - name: format
          weight: 0.3
          method: pattern
          expected: "Booking \\\\w+ confirmed"
          threshold: 1.0
        - name: completeness
          weight: 0.2
          method: similarity
          expected: "Your booking ABC123 is confirmed."
          threshold: 0.7
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

import yaml


_VALID_METHODS = frozenset(["keywords", "pattern", "similarity", "contains", "custom"])


@dataclass
class Criterion:
    """One evaluation dimension within a :class:`Rubric`.

    Attributes:
        name: Human-readable label (e.g. ``"correctness"``).
        weight: Contribution to the composite score (0.0–1.0).  Weights across
            all criteria in a rubric do **not** need to sum to 1.0 — the
            :class:`~mcptest.eval.grader.Grader` normalises them automatically.
        method: Grading strategy — one of ``"keywords"``, ``"pattern"``,
            ``"similarity"``, ``"contains"``, or ``"custom"``.
        expected: Reference material for the chosen method.  May be a single
            string or a list of strings (used by ``"keywords"`` and
            ``"similarity"`` with multiple references).
        threshold: Minimum score to consider this criterion *passed* (0.0–1.0).
            A score *equal to* the threshold is a pass.
        case_sensitive: When ``False`` (default), text comparison ignores case.
    """

    name: str
    weight: float
    method: str
    expected: Union[str, list[str]]
    threshold: float
    case_sensitive: bool = False

    def __post_init__(self) -> None:
        if self.method not in _VALID_METHODS:
            raise ValueError(
                f"criterion {self.name!r}: unknown method {self.method!r}. "
                f"Valid methods: {sorted(_VALID_METHODS)}"
            )
        if not (0.0 <= self.threshold <= 1.0):
            raise ValueError(
                f"criterion {self.name!r}: threshold must be in [0.0, 1.0], "
                f"got {self.threshold}"
            )
        if self.weight < 0.0:
            raise ValueError(
                f"criterion {self.name!r}: weight must be >= 0.0, got {self.weight}"
            )


@dataclass
class Rubric:
    """Named collection of evaluation criteria.

    Attributes:
        name: Identifier for this rubric (used in reports).
        criteria: One or more :class:`Criterion` objects to evaluate.
    """

    name: str
    criteria: list[Criterion] = field(default_factory=list)

    def total_weight(self) -> float:
        """Return the sum of all criterion weights."""
        return sum(c.weight for c in self.criteria)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_rubric(path: Path) -> Rubric:
    """Load a :class:`Rubric` from a standalone YAML file.

    The file must contain a top-level ``rubric:`` key::

        rubric:
          name: my-rubric
          criteria:
            - name: correctness
              ...

    Args:
        path: Absolute or relative path to the YAML file.

    Returns:
        A populated :class:`Rubric` instance.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the YAML structure is invalid.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"rubric file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "rubric" not in data:
        raise ValueError(
            f"rubric file {path} must contain a top-level 'rubric:' key"
        )
    return load_rubric_from_dict(data["rubric"])


def load_rubric_from_dict(data: dict) -> Rubric:
    """Construct a :class:`Rubric` from a plain dictionary (e.g. inline YAML).

    Args:
        data: Dictionary with keys ``name`` and ``criteria``.

    Returns:
        A populated :class:`Rubric` instance.

    Raises:
        ValueError: If required keys are missing or values are invalid.
    """
    if not isinstance(data, dict):
        raise ValueError(f"rubric definition must be a mapping, got {type(data).__name__}")

    name = data.get("name")
    if not name:
        raise ValueError("rubric must have a 'name' field")

    raw_criteria = data.get("criteria", [])
    if not isinstance(raw_criteria, list):
        raise ValueError("rubric 'criteria' must be a list")

    criteria: list[Criterion] = []
    for i, raw in enumerate(raw_criteria):
        if not isinstance(raw, dict):
            raise ValueError(f"criterion[{i}] must be a mapping, got {type(raw).__name__}")
        try:
            c = _criterion_from_dict(raw, index=i)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"criterion[{i}] is invalid: {exc}") from exc
        criteria.append(c)

    return Rubric(name=str(name), criteria=criteria)


def _criterion_from_dict(data: dict, index: int = 0) -> Criterion:
    """Build one :class:`Criterion` from a raw mapping."""
    missing = [k for k in ("name", "method", "expected", "weight", "threshold") if k not in data]
    if missing:
        raise ValueError(f"criterion is missing required fields: {missing}")

    expected = data["expected"]
    # Coerce a scalar to list for uniformity; grader handles both.
    if isinstance(expected, str):
        expected_val: Union[str, list[str]] = expected
    elif isinstance(expected, list):
        expected_val = [str(e) for e in expected]
    else:
        expected_val = str(expected)

    return Criterion(
        name=str(data["name"]),
        weight=float(data["weight"]),
        method=str(data["method"]),
        expected=expected_val,
        threshold=float(data["threshold"]),
        case_sensitive=bool(data.get("case_sensitive", False)),
    )
