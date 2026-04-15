"""Unit tests for the pure response-matching logic."""

from __future__ import annotations

import pytest

from mcptest.fixtures.models import Response
from mcptest.mock_server.matcher import NoMatchError, match_response


def _ok(**kwargs: object) -> Response:
    kwargs.setdefault("return_text", "ok")
    return Response.model_validate({**kwargs})


class TestExactMatch:
    def test_single_key(self) -> None:
        r = _ok(match={"repo": "acme/api"}, return_text="matched")
        fallback = _ok(default=True, return_text="fallback")
        assert match_response([r, fallback], {"repo": "acme/api"}) is r

    def test_multi_key_all_required(self) -> None:
        r = _ok(match={"repo": "a", "title": "b"}, return_text="matched")
        fallback = _ok(default=True)
        assert match_response([r, fallback], {"repo": "a", "title": "b"}) is r
        assert match_response([r, fallback], {"repo": "a", "title": "c"}) is fallback

    def test_extra_args_ignored(self) -> None:
        r = _ok(match={"repo": "a"})
        assert match_response([r], {"repo": "a", "extra": "x"}) is r

    def test_missing_arg(self) -> None:
        r = _ok(match={"repo": "a"})
        fallback = _ok(default=True)
        assert match_response([r, fallback], {}) is fallback

    def test_nested_dict(self) -> None:
        r = _ok(match={"meta": {"owner": "alice"}}, return_text="matched")
        fallback = _ok(default=True)
        assert match_response([r, fallback], {"meta": {"owner": "alice", "age": 30}}) is r
        assert match_response([r, fallback], {"meta": {"owner": "bob"}}) is fallback
        assert match_response([r, fallback], {"meta": "string"}) is fallback

    def test_list_prefix(self) -> None:
        r = _ok(match={"tags": ["bug"]}, return_text="matched")
        fallback = _ok(default=True)
        assert match_response([r, fallback], {"tags": ["bug", "p1"]}) is r
        assert match_response([r, fallback], {"tags": ["feature"]}) is fallback
        assert match_response([r, fallback], {"tags": []}) is fallback
        assert match_response([r, fallback], {"tags": "scalar"}) is fallback


class TestRegexMatch:
    def test_single_pattern(self) -> None:
        r = _ok(match_regex={"title": r"500"}, return_text="matched")
        fallback = _ok(default=True)
        assert match_response([r, fallback], {"title": "bug: 500 error"}) is r
        assert match_response([r, fallback], {"title": "feature request"}) is fallback

    def test_non_string_stringified(self) -> None:
        r = _ok(match_regex={"code": r"^4\d\d$"}, return_text="matched")
        fallback = _ok(default=True)
        assert match_response([r, fallback], {"code": 404}) is r
        assert match_response([r, fallback], {"code": 200}) is fallback

    def test_missing_key(self) -> None:
        r = _ok(match_regex={"title": r".*"})
        fallback = _ok(default=True)
        assert match_response([r, fallback], {}) is fallback

    def test_invalid_regex_does_not_match(self) -> None:
        r = _ok(match_regex={"title": r"([unclosed"})
        fallback = _ok(default=True)
        assert match_response([r, fallback], {"title": "anything"}) is fallback

    def test_combined_with_exact_match(self) -> None:
        r = _ok(
            match={"repo": "acme/api"},
            match_regex={"title": r"500"},
            return_text="matched",
        )
        fallback = _ok(default=True)
        assert (
            match_response([r, fallback], {"repo": "acme/api", "title": "got a 500"})
            is r
        )
        assert (
            match_response([r, fallback], {"repo": "other", "title": "got a 500"})
            is fallback
        )
        assert (
            match_response([r, fallback], {"repo": "acme/api", "title": "ok"})
            is fallback
        )


class TestOrdering:
    def test_first_match_wins(self) -> None:
        r1 = _ok(match={"x": 1}, return_text="one")
        r2 = _ok(match={"x": 1}, return_text="two")
        assert match_response([r1, r2], {"x": 1}) is r1

    def test_default_only_if_no_match(self) -> None:
        explicit = _ok(match={"x": 1}, return_text="explicit")
        default = _ok(default=True, return_text="default")
        assert match_response([default, explicit], {"x": 1}) is explicit
        assert match_response([default, explicit], {"x": 2}) is default

    def test_multiple_defaults_first_kept(self) -> None:
        d1 = _ok(default=True, return_text="first")
        d2 = _ok(default=True, return_text="second")
        assert match_response([d1, d2], {}) is d1


class TestNoMatch:
    def test_raises_without_default(self) -> None:
        r = _ok(match={"x": 1})
        with pytest.raises(NoMatchError):
            match_response([r], {"x": 2})

    def test_empty_list_raises(self) -> None:
        with pytest.raises(NoMatchError):
            match_response([], {"x": 1})

    def test_no_conditions_matches_everything(self) -> None:
        r = _ok(return_text="always")
        assert match_response([r], {}) is r
        assert match_response([r], {"anything": "goes"}) is r
