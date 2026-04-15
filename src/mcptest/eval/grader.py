"""Grading engine — scores agent output against a :class:`~mcptest.eval.rubric.Rubric`.

The :class:`Grader` applies each :class:`~mcptest.eval.rubric.Criterion` to a
piece of text and returns a detailed :class:`EvalResult` with per-criterion
scores, a weighted composite score, and an overall pass/fail verdict.

Grading methods
---------------
keywords
    Fraction of expected keywords found in the text
    (:func:`~mcptest.eval.similarity.keyword_coverage`).
pattern
    Binary regex match: 1.0 when the pattern matches anywhere in the text,
    0.0 otherwise.
similarity
    Best of levenshtein / jaccard / cosine similarity against a reference
    string (:func:`~mcptest.eval.similarity.best_similarity`).
contains
    Binary sub-string check: 1.0 when the expected string is present in the
    text, 0.0 otherwise.
custom
    Always returns 0.0 with a descriptive detail message (reserved for
    user-supplied plug-in graders).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mcptest.eval.rubric import Criterion, Rubric
from mcptest.eval.similarity import best_similarity, keyword_coverage

if TYPE_CHECKING:
    from mcptest.runner.trace import Trace


@dataclass
class CriterionResult:
    """Score for one :class:`~mcptest.eval.rubric.Criterion`.

    Attributes:
        criterion: The criterion name.
        score: Raw score in [0.0, 1.0].
        passed: ``True`` when ``score >= criterion.threshold``.
        detail: Human-readable explanation of how the score was computed.
    """

    criterion: str
    score: float
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "score": round(self.score, 4),
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class EvalResult:
    """Full evaluation of one text against a :class:`~mcptest.eval.rubric.Rubric`.

    Attributes:
        rubric: Name of the rubric used.
        criterion_results: Per-criterion scores.
        composite_score: Weighted average of criterion scores (0.0–1.0).
            Weights are normalised so they need not sum to 1.0 in the rubric.
        passed: ``True`` when *every* criterion passed.
    """

    rubric: str
    criterion_results: list[CriterionResult] = field(default_factory=list)
    composite_score: float = 0.0
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rubric": self.rubric,
            "composite_score": round(self.composite_score, 4),
            "passed": self.passed,
            "criteria": [r.to_dict() for r in self.criterion_results],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class Grader:
    """Evaluates text against a :class:`~mcptest.eval.rubric.Rubric`.

    Args:
        rubric: The rubric defining evaluation criteria.
    """

    def __init__(self, rubric: Rubric) -> None:
        self._rubric = rubric

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def grade(self, text: str) -> EvalResult:
        """Grade *text* against every criterion in the rubric.

        Args:
            text: The agent output string to evaluate.

        Returns:
            An :class:`EvalResult` with per-criterion scores and a composite.
        """
        results: list[CriterionResult] = []
        for criterion in self._rubric.criteria:
            result = self._grade_criterion(text, criterion)
            results.append(result)

        composite = self._weighted_composite(results)
        passed = all(r.passed for r in results)

        return EvalResult(
            rubric=self._rubric.name,
            criterion_results=results,
            composite_score=composite,
            passed=passed,
        )

    def grade_trace(self, trace: "Trace") -> EvalResult:
        """Grade the ``output`` field of *trace*.

        This is a convenience wrapper so callers can pass a
        :class:`~mcptest.runner.trace.Trace` directly without extracting its
        output string.

        Args:
            trace: A completed agent run.

        Returns:
            An :class:`EvalResult` computed from ``trace.output``.
        """
        return self.grade(trace.output)

    # ------------------------------------------------------------------
    # Per-method graders
    # ------------------------------------------------------------------

    def _grade_criterion(self, text: str, criterion: Criterion) -> CriterionResult:
        dispatch = {
            "keywords": self._grade_keywords,
            "pattern": self._grade_pattern,
            "similarity": self._grade_similarity,
            "contains": self._grade_contains,
            "custom": self._grade_custom,
        }
        grader_fn = dispatch.get(criterion.method, self._grade_custom)
        return grader_fn(text, criterion)

    def _grade_keywords(self, text: str, criterion: Criterion) -> CriterionResult:
        expected = criterion.expected
        keywords: list[str] = expected if isinstance(expected, list) else [expected]
        score = keyword_coverage(text, keywords, case_sensitive=criterion.case_sensitive)
        found = [kw for kw in keywords if (kw if criterion.case_sensitive else kw.lower()) in (text if criterion.case_sensitive else text.lower())]
        detail = (
            f"found {len(found)}/{len(keywords)} keywords: "
            f"{found!r} (missing: {[kw for kw in keywords if kw not in found]!r})"
        )
        return CriterionResult(
            criterion=criterion.name,
            score=score,
            passed=score >= criterion.threshold,
            detail=detail,
        )

    def _grade_pattern(self, text: str, criterion: Criterion) -> CriterionResult:
        pattern = criterion.expected if isinstance(criterion.expected, str) else criterion.expected[0]
        flags = 0 if criterion.case_sensitive else re.IGNORECASE
        try:
            match = re.search(pattern, text, flags)
        except re.error as exc:
            return CriterionResult(
                criterion=criterion.name,
                score=0.0,
                passed=False,
                detail=f"invalid regex pattern {pattern!r}: {exc}",
            )
        score = 1.0 if match else 0.0
        detail = (
            f"pattern {pattern!r} {'matched' if match else 'did not match'} in text"
        )
        return CriterionResult(
            criterion=criterion.name,
            score=score,
            passed=score >= criterion.threshold,
            detail=detail,
        )

    def _grade_similarity(self, text: str, criterion: Criterion) -> CriterionResult:
        references: list[str]
        if isinstance(criterion.expected, list):
            references = criterion.expected
        else:
            references = [criterion.expected]

        compare_text = text if criterion.case_sensitive else text.lower()
        scores = [
            best_similarity(
                compare_text,
                (ref if criterion.case_sensitive else ref.lower()),
            )
            for ref in references
        ]
        score = max(scores) if scores else 0.0
        best_ref = references[scores.index(score)] if scores else ""
        truncated = repr(best_ref[:60]) + ("..." if len(best_ref) > 60 else "")
        detail = f"best similarity {score:.4f} against reference {truncated}"
        return CriterionResult(
            criterion=criterion.name,
            score=score,
            passed=score >= criterion.threshold,
            detail=detail,
        )

    def _grade_contains(self, text: str, criterion: Criterion) -> CriterionResult:
        expected = criterion.expected if isinstance(criterion.expected, str) else criterion.expected[0]
        compare_text = text if criterion.case_sensitive else text.lower()
        compare_expected = expected if criterion.case_sensitive else expected.lower()
        found = compare_expected in compare_text
        score = 1.0 if found else 0.0
        detail = (
            f"substring {expected!r} {'found' if found else 'not found'} in text"
        )
        return CriterionResult(
            criterion=criterion.name,
            score=score,
            passed=score >= criterion.threshold,
            detail=detail,
        )

    def _grade_custom(self, text: str, criterion: Criterion) -> CriterionResult:  # noqa: ARG002
        return CriterionResult(
            criterion=criterion.name,
            score=0.0,
            passed=0.0 >= criterion.threshold,
            detail=(
                "custom grading method not implemented; "
                "override Grader._grade_criterion() to supply your own logic"
            ),
        )

    # ------------------------------------------------------------------
    # Score aggregation
    # ------------------------------------------------------------------

    def _weighted_composite(self, results: list[CriterionResult]) -> float:
        """Compute a weight-normalised composite score."""
        if not results:
            return 0.0
        # Match criterion weights by name from the rubric.
        weight_map = {c.name: c.weight for c in self._rubric.criteria}
        total_weight = sum(weight_map.get(r.criterion, 1.0) for r in results)
        if total_weight == 0.0:
            return 0.0
        weighted_sum = sum(
            r.score * weight_map.get(r.criterion, 1.0) for r in results
        )
        return weighted_sum / total_weight
