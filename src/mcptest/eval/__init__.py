"""mcptest eval — semantic evaluation with custom rubrics.

Score agent text output against named criteria without requiring LLM API calls.
Uses deterministic text similarity, pattern matching, and keyword coverage to
grade responses against a :class:`Rubric`.

Quick-start::

    from mcptest.eval import Grader, Rubric, Criterion

    rubric = Rubric(
        name="booking-quality",
        criteria=[
            Criterion(
                name="correctness",
                weight=0.6,
                method="keywords",
                expected=["confirmed", "booking_id"],
                threshold=0.5,
            ),
            Criterion(
                name="format",
                weight=0.4,
                method="pattern",
                expected=r"Booking \\w+ confirmed",
                threshold=1.0,
            ),
        ],
    )
    grader = Grader(rubric)
    result = grader.grade("Your booking ABC123 is confirmed.")
    print(result.passed, result.composite_score)

CLI::

    mcptest eval tests/ --rubric rubrics/booking.yaml
    mcptest eval tests/ --json
    mcptest eval tests/ --ci --fail-under 0.8
"""

from __future__ import annotations

from mcptest.eval.grader import CriterionResult, EvalResult, Grader
from mcptest.eval.report import EvalSummary, aggregate_results, render_eval_report
from mcptest.eval.rubric import Criterion, Rubric, load_rubric, load_rubric_from_dict

__all__ = [
    "Criterion",
    "CriterionResult",
    "EvalResult",
    "EvalSummary",
    "Grader",
    "Rubric",
    "aggregate_results",
    "load_rubric",
    "load_rubric_from_dict",
    "render_eval_report",
]
