"""Tests for mcptest.eval — semantic evaluation with custom rubrics.

Targets 100% branch coverage across:
- mcptest.eval.rubric    (Criterion, Rubric, load_rubric, load_rubric_from_dict)
- mcptest.eval.similarity (all 5 metric functions)
- mcptest.eval.grader    (Grader, CriterionResult, EvalResult — all 5 methods)
- mcptest.eval.report    (EvalSummary, aggregate_results, render_eval_report)
- eval_command           (json output, ci mode, rubric from file, inline)
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from rich.console import Console

from mcptest.eval import (
    Criterion,
    CriterionResult,
    EvalResult,
    EvalSummary,
    Grader,
    Rubric,
    aggregate_results,
    load_rubric,
    load_rubric_from_dict,
    render_eval_report,
)
from mcptest.eval.similarity import (
    best_similarity,
    cosine_similarity_tfidf,
    jaccard_similarity,
    keyword_coverage,
    levenshtein_similarity,
)
from mcptest.runner.trace import Trace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _console_capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, highlight=False, markup=True)
    return console, buf


def _make_criterion(
    name: str = "correctness",
    method: str = "keywords",
    expected: str | list[str] = ["hello", "world"],
    weight: float = 1.0,
    threshold: float = 0.5,
    case_sensitive: bool = False,
) -> Criterion:
    return Criterion(
        name=name,
        method=method,
        expected=expected,
        weight=weight,
        threshold=threshold,
        case_sensitive=case_sensitive,
    )


def _make_rubric(criteria: list[Criterion] | None = None) -> Rubric:
    if criteria is None:
        criteria = [_make_criterion()]
    return Rubric(name="test-rubric", criteria=criteria)


def _make_eval_result(
    rubric: str = "test-rubric",
    passed: bool = True,
    composite: float = 0.8,
    criteria: list[CriterionResult] | None = None,
) -> EvalResult:
    if criteria is None:
        criteria = [
            CriterionResult(criterion="correctness", score=0.8, passed=passed, detail="ok")
        ]
    return EvalResult(
        rubric=rubric,
        criterion_results=criteria,
        composite_score=composite,
        passed=passed,
    )


# ===========================================================================
# rubric.py tests
# ===========================================================================


class TestCriterion:
    def test_valid_criterion(self) -> None:
        c = _make_criterion(method="keywords", expected=["a", "b"])
        assert c.name == "correctness"
        assert c.weight == 1.0

    def test_invalid_method_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown method"):
            Criterion(name="x", weight=1.0, method="invalid", expected="y", threshold=0.5)

    def test_threshold_out_of_range_high(self) -> None:
        with pytest.raises(ValueError, match="threshold must be in"):
            Criterion(name="x", weight=1.0, method="keywords", expected="y", threshold=1.5)

    def test_threshold_out_of_range_low(self) -> None:
        with pytest.raises(ValueError, match="threshold must be in"):
            Criterion(name="x", weight=1.0, method="keywords", expected="y", threshold=-0.1)

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(ValueError, match="weight must be >= 0.0"):
            Criterion(name="x", weight=-1.0, method="keywords", expected="y", threshold=0.5)

    def test_all_valid_methods(self) -> None:
        for method in ("keywords", "pattern", "similarity", "contains", "custom"):
            c = Criterion(name="x", weight=1.0, method=method, expected="y", threshold=0.5)
            assert c.method == method

    def test_threshold_at_boundary_zero(self) -> None:
        c = Criterion(name="x", weight=1.0, method="keywords", expected="y", threshold=0.0)
        assert c.threshold == 0.0

    def test_threshold_at_boundary_one(self) -> None:
        c = Criterion(name="x", weight=1.0, method="keywords", expected="y", threshold=1.0)
        assert c.threshold == 1.0

    def test_zero_weight_is_valid(self) -> None:
        c = Criterion(name="x", weight=0.0, method="keywords", expected="y", threshold=0.5)
        assert c.weight == 0.0

    def test_case_sensitive_default_false(self) -> None:
        c = _make_criterion()
        assert c.case_sensitive is False


class TestRubric:
    def test_total_weight_single(self) -> None:
        rubric = _make_rubric([_make_criterion(weight=0.7)])
        assert rubric.total_weight() == pytest.approx(0.7)

    def test_total_weight_multiple(self) -> None:
        rubric = _make_rubric([
            _make_criterion(name="a", weight=0.5),
            _make_criterion(name="b", weight=0.3),
        ])
        assert rubric.total_weight() == pytest.approx(0.8)

    def test_total_weight_empty(self) -> None:
        rubric = Rubric(name="empty", criteria=[])
        assert rubric.total_weight() == 0.0


class TestLoadRubricFromDict:
    def test_valid_dict(self) -> None:
        data = {
            "name": "test",
            "criteria": [
                {
                    "name": "correctness",
                    "weight": 0.5,
                    "method": "keywords",
                    "expected": ["ok", "done"],
                    "threshold": 0.5,
                }
            ],
        }
        rubric = load_rubric_from_dict(data)
        assert rubric.name == "test"
        assert len(rubric.criteria) == 1
        assert rubric.criteria[0].name == "correctness"

    def test_missing_name_raises(self) -> None:
        with pytest.raises(ValueError, match="must have a 'name'"):
            load_rubric_from_dict({"criteria": []})

    def test_non_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            load_rubric_from_dict("not a dict")  # type: ignore[arg-type]

    def test_criteria_not_list_raises(self) -> None:
        with pytest.raises(ValueError, match="'criteria' must be a list"):
            load_rubric_from_dict({"name": "x", "criteria": "not-a-list"})

    def test_criterion_not_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            load_rubric_from_dict({"name": "x", "criteria": ["string-not-dict"]})

    def test_criterion_missing_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required fields"):
            load_rubric_from_dict({
                "name": "x",
                "criteria": [{"name": "c", "method": "keywords"}],  # missing weight/expected/threshold
            })

    def test_string_expected_is_kept(self) -> None:
        data = {
            "name": "test",
            "criteria": [
                {"name": "c", "weight": 1.0, "method": "pattern", "expected": "hello", "threshold": 1.0}
            ],
        }
        rubric = load_rubric_from_dict(data)
        assert rubric.criteria[0].expected == "hello"

    def test_list_expected_is_cast_to_str_list(self) -> None:
        data = {
            "name": "test",
            "criteria": [
                {"name": "c", "weight": 1.0, "method": "keywords", "expected": [1, 2], "threshold": 0.5}
            ],
        }
        rubric = load_rubric_from_dict(data)
        assert rubric.criteria[0].expected == ["1", "2"]

    def test_non_list_non_str_expected_cast(self) -> None:
        data = {
            "name": "test",
            "criteria": [
                {"name": "c", "weight": 1.0, "method": "keywords", "expected": 42, "threshold": 0.5}
            ],
        }
        rubric = load_rubric_from_dict(data)
        assert rubric.criteria[0].expected == "42"

    def test_empty_criteria_list_is_valid(self) -> None:
        rubric = load_rubric_from_dict({"name": "empty", "criteria": []})
        assert rubric.criteria == []

    def test_case_sensitive_field_propagated(self) -> None:
        data = {
            "name": "test",
            "criteria": [
                {"name": "c", "weight": 1.0, "method": "keywords", "expected": ["x"], "threshold": 0.5, "case_sensitive": True}
            ],
        }
        rubric = load_rubric_from_dict(data)
        assert rubric.criteria[0].case_sensitive is True

    def test_invalid_criterion_method_raises(self) -> None:
        data = {
            "name": "test",
            "criteria": [
                {"name": "c", "weight": 1.0, "method": "bogus", "expected": "x", "threshold": 0.5}
            ],
        }
        with pytest.raises(ValueError, match="criterion\\[0\\] is invalid"):
            load_rubric_from_dict(data)


class TestLoadRubricFromFile:
    def test_valid_file(self, tmp_path: Path) -> None:
        p = tmp_path / "rubric.yaml"
        p.write_text(
            "rubric:\n"
            "  name: file-rubric\n"
            "  criteria:\n"
            "    - name: c\n"
            "      weight: 1.0\n"
            "      method: keywords\n"
            "      expected: [hello]\n"
            "      threshold: 0.5\n"
        )
        rubric = load_rubric(p)
        assert rubric.name == "file-rubric"

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="rubric file not found"):
            load_rubric(tmp_path / "nonexistent.yaml")

    def test_missing_rubric_key_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "rubric.yaml"
        p.write_text("name: test\n")
        with pytest.raises(ValueError, match="top-level 'rubric:' key"):
            load_rubric(p)

    def test_non_dict_content_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "rubric.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="top-level 'rubric:' key"):
            load_rubric(p)


# ===========================================================================
# similarity.py tests
# ===========================================================================


class TestLevenshteinSimilarity:
    def test_identical_strings(self) -> None:
        assert levenshtein_similarity("hello", "hello") == 1.0

    def test_empty_strings(self) -> None:
        assert levenshtein_similarity("", "") == 1.0

    def test_one_empty(self) -> None:
        assert levenshtein_similarity("abc", "") == 0.0
        assert levenshtein_similarity("", "abc") == 0.0

    def test_single_char_diff(self) -> None:
        score = levenshtein_similarity("abc", "abd")
        assert 0.0 < score < 1.0

    def test_completely_different(self) -> None:
        score = levenshtein_similarity("aaa", "bbb")
        assert score == pytest.approx(0.0)

    def test_partial_overlap(self) -> None:
        score = levenshtein_similarity("hello world", "hello earth")
        assert 0.0 < score < 1.0


class TestJaccardSimilarity:
    def test_identical_strings(self) -> None:
        assert jaccard_similarity("hello world", "hello world") == pytest.approx(1.0)

    def test_both_empty(self) -> None:
        assert jaccard_similarity("", "") == pytest.approx(1.0)

    def test_one_empty(self) -> None:
        assert jaccard_similarity("hello", "") == 0.0
        assert jaccard_similarity("", "world") == 0.0

    def test_no_overlap(self) -> None:
        assert jaccard_similarity("cat", "dog") == pytest.approx(0.0)

    def test_partial_overlap(self) -> None:
        score = jaccard_similarity("hello world", "hello earth")
        assert 0.0 < score < 1.0

    def test_case_insensitive(self) -> None:
        assert jaccard_similarity("Hello", "hello") == pytest.approx(1.0)

    def test_subset(self) -> None:
        score = jaccard_similarity("hello", "hello world")
        assert 0.0 < score < 1.0


class TestCosineSimilarityTFIDF:
    def test_identical_strings(self) -> None:
        assert cosine_similarity_tfidf("hello world", "hello world") == pytest.approx(1.0)

    def test_both_empty(self) -> None:
        assert cosine_similarity_tfidf("", "") == pytest.approx(1.0)

    def test_one_empty(self) -> None:
        assert cosine_similarity_tfidf("hello", "") == 0.0
        assert cosine_similarity_tfidf("", "world") == 0.0

    def test_no_overlap(self) -> None:
        assert cosine_similarity_tfidf("cat", "dog") == pytest.approx(0.0)

    def test_partial_overlap(self) -> None:
        score = cosine_similarity_tfidf("hello world", "hello earth")
        assert 0.0 < score < 1.0

    def test_case_insensitive(self) -> None:
        assert cosine_similarity_tfidf("Hello World", "hello world") == pytest.approx(1.0)

    def test_repeated_words(self) -> None:
        score = cosine_similarity_tfidf("the the the", "the the")
        assert score > 0.9


class TestKeywordCoverage:
    def test_all_found(self) -> None:
        assert keyword_coverage("hello world foo", ["hello", "world"]) == 1.0

    def test_none_found(self) -> None:
        assert keyword_coverage("hello world", ["missing", "gone"]) == 0.0

    def test_partial(self) -> None:
        assert keyword_coverage("hello world", ["hello", "missing"]) == pytest.approx(0.5)

    def test_empty_keywords_returns_one(self) -> None:
        assert keyword_coverage("any text", []) == 1.0

    def test_case_insensitive_default(self) -> None:
        assert keyword_coverage("Hello World", ["hello", "world"]) == 1.0

    def test_case_sensitive_miss(self) -> None:
        assert keyword_coverage("Hello World", ["hello"], case_sensitive=True) == 0.0

    def test_case_sensitive_hit(self) -> None:
        assert keyword_coverage("Hello World", ["Hello"], case_sensitive=True) == 1.0

    def test_empty_text(self) -> None:
        assert keyword_coverage("", ["hello"]) == 0.0


class TestBestSimilarity:
    def test_identical(self) -> None:
        assert best_similarity("hello", "hello") == pytest.approx(1.0)

    def test_partial(self) -> None:
        score = best_similarity("hello world", "hello earth")
        assert 0.0 < score <= 1.0

    def test_empty_strings(self) -> None:
        # All three metrics return 1.0 for two empty strings
        assert best_similarity("", "") == pytest.approx(1.0)


# ===========================================================================
# grader.py tests
# ===========================================================================


class TestCriterionResult:
    def test_to_dict(self) -> None:
        cr = CriterionResult(criterion="c", score=0.75, passed=True, detail="ok")
        d = cr.to_dict()
        assert d["criterion"] == "c"
        assert d["score"] == pytest.approx(0.75)
        assert d["passed"] is True
        assert d["detail"] == "ok"


class TestEvalResult:
    def test_to_dict(self) -> None:
        er = _make_eval_result()
        d = er.to_dict()
        assert "rubric" in d
        assert "composite_score" in d
        assert "passed" in d
        assert "criteria" in d

    def test_to_json(self) -> None:
        er = _make_eval_result()
        j = er.to_json()
        parsed = json.loads(j)
        assert parsed["rubric"] == "test-rubric"


class TestGraderKeywords:
    def test_all_found_passes(self) -> None:
        rubric = _make_rubric([_make_criterion(method="keywords", expected=["confirmed", "booking"])])
        grader = Grader(rubric)
        result = grader.grade("Your booking is confirmed.")
        cr = result.criterion_results[0]
        assert cr.score == 1.0
        assert cr.passed is True

    def test_partial_below_threshold_fails(self) -> None:
        rubric = _make_rubric([_make_criterion(method="keywords", expected=["a", "b", "c"], threshold=0.9)])
        grader = Grader(rubric)
        result = grader.grade("text with a only")
        cr = result.criterion_results[0]
        assert cr.score < 0.9
        assert cr.passed is False

    def test_none_found_zero(self) -> None:
        rubric = _make_rubric([_make_criterion(method="keywords", expected=["xyz", "abc"])])
        grader = Grader(rubric)
        result = grader.grade("nothing relevant here")
        assert result.criterion_results[0].score == 0.0

    def test_single_string_expected(self) -> None:
        rubric = _make_rubric([_make_criterion(method="keywords", expected="hello")])
        grader = Grader(rubric)
        result = grader.grade("hello world")
        assert result.criterion_results[0].score == 1.0

    def test_case_sensitive_miss(self) -> None:
        c = _make_criterion(method="keywords", expected=["Hello"], case_sensitive=True)
        grader = Grader(_make_rubric([c]))
        result = grader.grade("hello world")
        assert result.criterion_results[0].score == 0.0

    def test_case_sensitive_hit(self) -> None:
        c = _make_criterion(method="keywords", expected=["Hello"], case_sensitive=True)
        grader = Grader(_make_rubric([c]))
        result = grader.grade("Hello world")
        assert result.criterion_results[0].score == 1.0

    def test_threshold_equal_score_passes(self) -> None:
        rubric = _make_rubric([_make_criterion(method="keywords", expected=["found", "missing"], threshold=0.5)])
        grader = Grader(rubric)
        result = grader.grade("only found here")
        cr = result.criterion_results[0]
        # score = 0.5 (1 of 2 keywords), threshold = 0.5 → passed
        assert cr.score == pytest.approx(0.5)
        assert cr.passed is True


class TestGraderPattern:
    def test_match_passes(self) -> None:
        rubric = _make_rubric([_make_criterion(method="pattern", expected=r"Booking \w+ confirmed")])
        grader = Grader(rubric)
        result = grader.grade("Booking ABC123 confirmed, thank you.")
        assert result.criterion_results[0].score == 1.0
        assert result.criterion_results[0].passed is True

    def test_no_match_fails(self) -> None:
        rubric = _make_rubric([_make_criterion(method="pattern", expected=r"Booking \w+ confirmed")])
        grader = Grader(rubric)
        result = grader.grade("Your order is ready.")
        assert result.criterion_results[0].score == 0.0

    def test_case_insensitive_default(self) -> None:
        rubric = _make_rubric([_make_criterion(method="pattern", expected="HELLO")])
        grader = Grader(rubric)
        result = grader.grade("hello world")
        assert result.criterion_results[0].score == 1.0

    def test_case_sensitive_miss(self) -> None:
        c = _make_criterion(method="pattern", expected="HELLO", case_sensitive=True)
        grader = Grader(_make_rubric([c]))
        result = grader.grade("hello world")
        assert result.criterion_results[0].score == 0.0

    def test_invalid_regex_scores_zero(self) -> None:
        rubric = _make_rubric([_make_criterion(method="pattern", expected="[invalid")])
        grader = Grader(rubric)
        result = grader.grade("hello world")
        cr = result.criterion_results[0]
        assert cr.score == 0.0
        assert "invalid regex" in cr.detail

    def test_list_expected_uses_first(self) -> None:
        rubric = _make_rubric([_make_criterion(method="pattern", expected=["hello", "world"])])
        grader = Grader(rubric)
        result = grader.grade("hello there")
        assert result.criterion_results[0].score == 1.0


class TestGraderSimilarity:
    def test_high_similarity_passes(self) -> None:
        ref = "Your booking is confirmed."
        rubric = _make_rubric([_make_criterion(method="similarity", expected=ref, threshold=0.5)])
        grader = Grader(rubric)
        result = grader.grade("Your booking is confirmed.")
        assert result.criterion_results[0].score == pytest.approx(1.0)
        assert result.criterion_results[0].passed is True

    def test_low_similarity_fails(self) -> None:
        rubric = _make_rubric([_make_criterion(method="similarity", expected="booking confirmed", threshold=0.9)])
        grader = Grader(rubric)
        result = grader.grade("completely unrelated text here")
        cr = result.criterion_results[0]
        assert cr.passed is False

    def test_multiple_references_best_chosen(self) -> None:
        rubric = _make_rubric([
            _make_criterion(
                method="similarity",
                expected=["completely wrong answer", "booking ABC123 is confirmed"],
                threshold=0.0,
            )
        ])
        grader = Grader(rubric)
        result = grader.grade("booking ABC123 is confirmed")
        cr = result.criterion_results[0]
        # Should pick the better reference
        assert cr.score > 0.5

    def test_case_insensitive_comparison(self) -> None:
        rubric = _make_rubric([_make_criterion(method="similarity", expected="HELLO WORLD", threshold=0.5)])
        grader = Grader(rubric)
        result = grader.grade("hello world")
        assert result.criterion_results[0].score == pytest.approx(1.0)

    def test_case_sensitive_comparison(self) -> None:
        c = _make_criterion(method="similarity", expected="HELLO", case_sensitive=True, threshold=0.5)
        grader = Grader(_make_rubric([c]))
        result = grader.grade("hello")
        # jaccard/cosine tokenizers always lowercase, so "hello" vs "HELLO" still
        # matches at full similarity via those metrics even with case_sensitive=True.
        # best_similarity picks the max across all three metrics.
        assert result.criterion_results[0].score == pytest.approx(1.0)


class TestGraderContains:
    def test_found_passes(self) -> None:
        rubric = _make_rubric([_make_criterion(method="contains", expected="confirmed")])
        grader = Grader(rubric)
        result = grader.grade("Your booking is confirmed.")
        assert result.criterion_results[0].score == 1.0
        assert result.criterion_results[0].passed is True

    def test_not_found_fails(self) -> None:
        rubric = _make_rubric([_make_criterion(method="contains", expected="confirmed")])
        grader = Grader(rubric)
        result = grader.grade("Your order is pending.")
        assert result.criterion_results[0].score == 0.0

    def test_case_insensitive_default(self) -> None:
        rubric = _make_rubric([_make_criterion(method="contains", expected="CONFIRMED")])
        grader = Grader(rubric)
        result = grader.grade("booking confirmed")
        assert result.criterion_results[0].score == 1.0

    def test_case_sensitive_miss(self) -> None:
        c = _make_criterion(method="contains", expected="CONFIRMED", case_sensitive=True)
        grader = Grader(_make_rubric([c]))
        result = grader.grade("booking confirmed")
        assert result.criterion_results[0].score == 0.0

    def test_list_expected_uses_first(self) -> None:
        rubric = _make_rubric([_make_criterion(method="contains", expected=["confirmed", "done"])])
        grader = Grader(rubric)
        result = grader.grade("booking confirmed")
        assert result.criterion_results[0].score == 1.0


class TestGraderCustom:
    def test_custom_returns_zero_by_default(self) -> None:
        rubric = _make_rubric([_make_criterion(method="custom")])
        grader = Grader(rubric)
        result = grader.grade("any text")
        cr = result.criterion_results[0]
        assert cr.score == 0.0
        assert "custom grading" in cr.detail

    def test_custom_threshold_zero_passes(self) -> None:
        c = _make_criterion(method="custom", threshold=0.0)
        grader = Grader(_make_rubric([c]))
        result = grader.grade("text")
        # 0.0 >= 0.0 → passed
        assert result.criterion_results[0].passed is True

    def test_custom_threshold_nonzero_fails(self) -> None:
        c = _make_criterion(method="custom", threshold=0.1)
        grader = Grader(_make_rubric([c]))
        result = grader.grade("text")
        assert result.criterion_results[0].passed is False


class TestGraderComposite:
    def test_weighted_composite_single(self) -> None:
        c = _make_criterion(method="keywords", expected=["hello", "world"], weight=1.0, threshold=0.0)
        grader = Grader(_make_rubric([c]))
        result = grader.grade("hello world")
        assert result.composite_score == pytest.approx(1.0)

    def test_weighted_composite_multi(self) -> None:
        criteria = [
            _make_criterion(name="a", method="contains", expected="yes", weight=0.7, threshold=0.0),
            _make_criterion(name="b", method="contains", expected="no", weight=0.3, threshold=0.0),
        ]
        grader = Grader(_make_rubric(criteria))
        result = grader.grade("yes it is here")
        # a=1.0, b=0.0 → weighted = (1.0*0.7 + 0.0*0.3)/1.0 = 0.7
        assert result.composite_score == pytest.approx(0.7)

    def test_passed_requires_all_criteria(self) -> None:
        criteria = [
            _make_criterion(name="a", method="contains", expected="confirmed", weight=1.0, threshold=1.0),
            _make_criterion(name="b", method="contains", expected="absent_keyword", weight=1.0, threshold=1.0),
        ]
        grader = Grader(_make_rubric(criteria))
        result = grader.grade("booking confirmed only")
        assert result.passed is False

    def test_empty_rubric_composite_zero(self) -> None:
        grader = Grader(Rubric(name="empty", criteria=[]))
        result = grader.grade("any text")
        assert result.composite_score == 0.0
        assert result.passed is True  # vacuously all criteria passed


class TestGraderTrace:
    def test_grade_trace_uses_output(self) -> None:
        rubric = _make_rubric([_make_criterion(method="contains", expected="confirmed")])
        grader = Grader(rubric)
        trace = Trace(output="Your booking is confirmed.")
        result = grader.grade_trace(trace)
        assert result.criterion_results[0].score == 1.0

    def test_grade_trace_empty_output(self) -> None:
        rubric = _make_rubric([_make_criterion(method="keywords", expected=["hello"])])
        grader = Grader(rubric)
        trace = Trace(output="")
        result = grader.grade_trace(trace)
        assert result.criterion_results[0].score == 0.0

    def test_grade_trace_returns_eval_result(self) -> None:
        grader = Grader(_make_rubric())
        trace = Trace(output="hello world text")
        result = grader.grade_trace(trace)
        assert isinstance(result, EvalResult)
        assert result.rubric == "test-rubric"


class TestGraderWeightNormalization:
    def test_weights_not_summing_to_one_normalized(self) -> None:
        criteria = [
            _make_criterion(name="a", method="contains", expected="yes", weight=2.0, threshold=0.0),
            _make_criterion(name="b", method="contains", expected="no", weight=2.0, threshold=0.0),
        ]
        grader = Grader(_make_rubric(criteria))
        result = grader.grade("yes here")
        # a=1.0 (weight 2), b=0.0 (weight 2) → (1.0*2 + 0.0*2) / 4 = 0.5
        assert result.composite_score == pytest.approx(0.5)

    def test_zero_weight_criteria_excluded_from_composite(self) -> None:
        criteria = [
            _make_criterion(name="a", method="contains", expected="yes", weight=1.0, threshold=0.0),
            _make_criterion(name="b", method="contains", expected="no", weight=0.0, threshold=0.0),
        ]
        grader = Grader(_make_rubric(criteria))
        result = grader.grade("yes here")
        # a=1.0 (weight 1), b=0.0 (weight 0) → (1.0*1 + 0.0*0) / 1 = 1.0
        assert result.composite_score == pytest.approx(1.0)


# ===========================================================================
# report.py tests
# ===========================================================================


class TestAggregateResults:
    def test_empty_list(self) -> None:
        summary = aggregate_results([])
        assert summary.total_cases == 0
        assert summary.passed_cases == 0
        assert summary.pass_rate == 0.0
        assert summary.mean_composite == 0.0
        assert summary.per_criterion == {}

    def test_single_result_passed(self) -> None:
        result = _make_eval_result(passed=True, composite=0.9)
        summary = aggregate_results([result])
        assert summary.total_cases == 1
        assert summary.passed_cases == 1
        assert summary.pass_rate == pytest.approx(1.0)
        assert summary.mean_composite == pytest.approx(0.9)

    def test_single_result_failed(self) -> None:
        result = _make_eval_result(passed=False, composite=0.3)
        summary = aggregate_results([result])
        assert summary.passed_cases == 0
        assert summary.pass_rate == 0.0

    def test_multiple_results_mixed(self) -> None:
        results = [
            _make_eval_result(passed=True, composite=1.0),
            _make_eval_result(passed=False, composite=0.0),
        ]
        summary = aggregate_results(results)
        assert summary.total_cases == 2
        assert summary.passed_cases == 1
        assert summary.pass_rate == pytest.approx(0.5)
        assert summary.mean_composite == pytest.approx(0.5)

    def test_per_criterion_averages(self) -> None:
        criteria_a = [CriterionResult("c1", 1.0, True, "ok"), CriterionResult("c2", 0.5, True, "ok")]
        criteria_b = [CriterionResult("c1", 0.5, True, "ok"), CriterionResult("c2", 1.0, True, "ok")]
        results = [
            EvalResult("rubric", criteria_a, 0.75, True),
            EvalResult("rubric", criteria_b, 0.75, True),
        ]
        summary = aggregate_results(results)
        assert summary.per_criterion["c1"] == pytest.approx(0.75)
        assert summary.per_criterion["c2"] == pytest.approx(0.75)

    def test_all_passed(self) -> None:
        results = [_make_eval_result(passed=True) for _ in range(5)]
        summary = aggregate_results(results)
        assert summary.pass_rate == 1.0

    def test_all_failed(self) -> None:
        results = [_make_eval_result(passed=False, composite=0.0) for _ in range(3)]
        summary = aggregate_results(results)
        assert summary.pass_rate == 0.0


class TestEvalSummary:
    def test_to_dict(self) -> None:
        summary = EvalSummary(
            total_cases=2,
            passed_cases=1,
            pass_rate=0.5,
            mean_composite=0.6,
            per_criterion={"c": 0.6},
            results=[_make_eval_result()],
        )
        d = summary.to_dict()
        assert d["total_cases"] == 2
        assert d["passed_cases"] == 1
        assert d["pass_rate"] == pytest.approx(0.5)
        assert d["mean_composite"] == pytest.approx(0.6)
        assert "c" in d["per_criterion"]
        assert len(d["results"]) == 1

    def test_to_json(self) -> None:
        summary = EvalSummary(
            total_cases=1,
            passed_cases=1,
            pass_rate=1.0,
            mean_composite=0.9,
        )
        j = summary.to_json()
        parsed = json.loads(j)
        assert parsed["total_cases"] == 1


class TestRenderEvalReport:
    def test_empty_results(self) -> None:
        console, buf = _console_capture()
        summary = EvalSummary(
            total_cases=0, passed_cases=0, pass_rate=0.0, mean_composite=0.0
        )
        render_eval_report(console, summary)
        output = buf.getvalue()
        assert "no evaluation results" in output

    def test_all_passed_verdict(self) -> None:
        console, buf = _console_capture()
        results = [_make_eval_result(passed=True, composite=1.0)]
        summary = aggregate_results(results)
        render_eval_report(console, summary)
        output = buf.getvalue()
        assert "PASS" in output

    def test_partial_pass_verdict(self) -> None:
        console, buf = _console_capture()
        mixed_criteria = [
            CriterionResult("c1", 1.0, True, "ok"),
            CriterionResult("c1", 0.0, False, "fail"),
        ]
        results = [
            EvalResult("rubric", [CriterionResult("c1", 1.0, True, "ok")], 1.0, True),
            EvalResult("rubric", [CriterionResult("c1", 0.0, False, "fail")], 0.0, False),
        ]
        summary = aggregate_results(results)
        render_eval_report(console, summary)
        output = buf.getvalue()
        assert "PARTIAL" in output

    def test_all_failed_verdict(self) -> None:
        console, buf = _console_capture()
        results = [
            EvalResult("rubric", [CriterionResult("c1", 0.0, False, "fail")], 0.0, False)
        ]
        summary = aggregate_results(results)
        render_eval_report(console, summary)
        output = buf.getvalue()
        assert "FAIL" in output

    def test_output_contains_rubric_name(self) -> None:
        console, buf = _console_capture()
        results = [_make_eval_result(rubric="my-special-rubric")]
        summary = aggregate_results(results)
        render_eval_report(console, summary)
        assert "my-special-rubric" in buf.getvalue()

    def test_output_contains_overall_line(self) -> None:
        console, buf = _console_capture()
        results = [_make_eval_result(passed=True, composite=0.85)]
        summary = aggregate_results(results)
        render_eval_report(console, summary)
        output = buf.getvalue()
        assert "Overall" in output


# ===========================================================================
# CLI eval_command tests
# ===========================================================================


def _write_fixture(tmp_path: Path) -> Path:
    p = tmp_path / "fixture.yaml"
    p.write_text(
        "server: { name: mock-test }\n"
        "tools:\n"
        "  - name: ping\n"
        "    responses:\n"
        "      - return_text: pong\n"
    )
    return p


def _write_test_suite(tmp_path: Path, fixture_path: Path, eval_section: str = "") -> Path:
    p = tmp_path / "test_suite.yaml"
    content = (
        "name: eval-test-suite\n"
        f"fixtures:\n"
        f"  - {fixture_path}\n"
        "agent:\n"
        "  command: echo 'Your booking is confirmed booking_id receipt'\n"
        "cases:\n"
        "  - name: case-one\n"
        "    input: hello\n"
    )
    if eval_section:
        content += eval_section
    p.write_text(content)
    return p


def _write_rubric_file(tmp_path: Path) -> Path:
    p = tmp_path / "rubric.yaml"
    p.write_text(
        "rubric:\n"
        "  name: test-rubric\n"
        "  criteria:\n"
        "    - name: correctness\n"
        "      weight: 1.0\n"
        "      method: keywords\n"
        "      expected: [confirmed, booking_id]\n"
        "      threshold: 0.5\n"
    )
    return p


class TestEvalCommand:
    def test_no_test_files(self, tmp_path: Path) -> None:
        from mcptest.cli.main import main

        runner = CliRunner()
        result = runner.invoke(main, ["eval", str(tmp_path / "nonexistent")])
        assert result.exit_code == 0
        assert "no test files found" in result.output

    def test_rubric_file_not_found(self, tmp_path: Path) -> None:
        from mcptest.cli.main import main

        fix = _write_fixture(tmp_path)
        suite = _write_test_suite(tmp_path, fix)

        runner = CliRunner()
        result = runner.invoke(main, ["eval", str(tmp_path), "--rubric", str(tmp_path / "missing.yaml")])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_no_rubric_no_inline_emits_warning(self, tmp_path: Path) -> None:
        from mcptest.cli.main import main

        fix = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fix)

        runner = CliRunner()
        result = runner.invoke(main, ["eval", str(tmp_path)])
        assert result.exit_code == 0
        assert "no rubric found" in result.output

    def test_rubric_from_file_json_output(self, tmp_path: Path) -> None:
        from mcptest.cli.main import main

        fix = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fix)
        rubric_path = _write_rubric_file(tmp_path)

        runner = CliRunner()
        result = runner.invoke(
            main, ["eval", str(tmp_path), "--rubric", str(rubric_path), "--json"]
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "total_cases" in parsed
        assert parsed["total_cases"] >= 1

    def test_inline_eval_section(self, tmp_path: Path) -> None:
        from mcptest.cli.main import main

        fix = _write_fixture(tmp_path)
        eval_section = (
            "    eval:\n"
            "      name: inline-rubric\n"
            "      criteria:\n"
            "        - name: presence\n"
            "          weight: 1.0\n"
            "          method: contains\n"
            "          expected: confirmed\n"
            "          threshold: 1.0\n"
        )
        _write_test_suite(tmp_path, fix, eval_section=eval_section)

        runner = CliRunner()
        result = runner.invoke(main, ["eval", str(tmp_path), "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "total_cases" in parsed

    def test_ci_mode_exits_nonzero_on_failure(self, tmp_path: Path) -> None:
        from mcptest.cli.main import main

        fix = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fix)
        # Rubric with impossible threshold
        p = tmp_path / "hard_rubric.yaml"
        p.write_text(
            "rubric:\n"
            "  name: hard\n"
            "  criteria:\n"
            "    - name: impossible\n"
            "      weight: 1.0\n"
            "      method: contains\n"
            "      expected: 'this text will never appear in echo output xyzzy9999'\n"
            "      threshold: 1.0\n"
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["eval", str(tmp_path), "--rubric", str(p), "--ci"],
        )
        assert result.exit_code == 1

    def test_ci_fail_under_threshold(self, tmp_path: Path) -> None:
        from mcptest.cli.main import main

        fix = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fix)
        rubric_path = _write_rubric_file(tmp_path)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["eval", str(tmp_path), "--rubric", str(rubric_path), "--ci", "--fail-under", "2.0"],
        )
        # fail-under 2.0 is impossible (max composite is 1.0) → exit 1
        assert result.exit_code == 1

    def test_rich_table_output(self, tmp_path: Path) -> None:
        from mcptest.cli.main import main

        fix = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fix)
        rubric_path = _write_rubric_file(tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["eval", str(tmp_path), "--rubric", str(rubric_path)])
        assert result.exit_code == 0
        # Rich table should mention the rubric and Overall
        assert "test-rubric" in result.output or "Overall" in result.output

    def test_rubric_file_load_error(self, tmp_path: Path) -> None:
        from mcptest.cli.main import main

        fix = _write_fixture(tmp_path)
        _write_test_suite(tmp_path, fix)

        bad_rubric = tmp_path / "bad.yaml"
        bad_rubric.write_text("rubric:\n  name: bad\n  criteria: not-a-list\n")

        runner = CliRunner()
        result = runner.invoke(main, ["eval", str(tmp_path), "--rubric", str(bad_rubric)])
        assert result.exit_code == 1
        assert "could not load rubric" in result.output
