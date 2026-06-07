from http import HTTPStatus

import pytest

from pylibinfra.httpkit import (
    PROBLEM_CONTENT_TYPE,
    APIError,
    ErrorMapping,
    bad_request,
    conflict,
    error,
    forbidden,
    internal,
    map_err,
    map_sentinel,
    not_found,
    unauthorized,
)


def test_error_builds_fields() -> None:
    e = error("TEST_CODE", HTTPStatus.BAD_REQUEST, "bad input")
    body = e.to_body()
    assert body["code"] == "TEST_CODE"
    assert body["status"] == 400
    assert body["title"] == "Bad Request"
    assert body["detail"] == "bad input"


def test_to_body_omits_empty() -> None:
    body = error("X", HTTPStatus.BAD_REQUEST, "d").to_body()
    assert "type" not in body
    assert "instance" not in body


def test_with_defaults_sets_type_and_title() -> None:
    e = APIError(status=400, detail="d")
    got = e.with_defaults()
    assert got.type == "about:blank"
    assert got.title == "Bad Request"
    body = got.to_body()
    assert body["type"] == "about:blank"


def test_with_defaults_keeps_explicit_type() -> None:
    e = APIError(type="https://errors.example/foo", status=400)
    assert e.with_defaults().type == "https://errors.example/foo"


def test_problem_content_type() -> None:
    assert PROBLEM_CONTENT_TYPE == "application/problem+json"


@pytest.mark.parametrize(
    ("fn", "code"),
    [
        (bad_request, 400),
        (unauthorized, 401),
        (forbidden, 403),
        (not_found, 404),
        (conflict, 409),
        (internal, 500),
    ],
)
def test_status_builders(fn: object, code: int) -> None:
    e = fn("c", "d")  # type: ignore[operator]
    assert e.status == code


_SENTINEL = RuntimeError("sentinel error")


class _TypedError(Exception):
    pass


def test_map_err_nil_returns_none() -> None:
    assert map_err(None) is None


def test_map_err_match_returns_api_error() -> None:
    mapped = map_err(
        _SENTINEL,
        ErrorMapping(
            match=lambda e: e is _SENTINEL,
            code="SENTINEL",
            status=404,
            detail="not found",
        ),
    )
    assert isinstance(mapped, APIError)
    assert mapped.code == "SENTINEL"


def test_map_err_no_match_returns_original() -> None:
    orig = ValueError("other")
    assert map_err(orig) is orig


def test_map_sentinel_nil_returns_none() -> None:
    assert map_sentinel(None, _SENTINEL, "C", 404, "d") is None


def test_map_sentinel_instance_match() -> None:
    mapped = map_sentinel(_SENTINEL, _SENTINEL, "SENT", 404, "detail")
    assert isinstance(mapped, APIError)
    assert mapped.status == 404


def test_map_sentinel_type_match() -> None:
    mapped = map_sentinel(_TypedError("typed"), _TypedError, "TYPED", 400, "typed")
    assert isinstance(mapped, APIError)
    assert mapped.status == 400


def test_map_sentinel_no_match_returns_original() -> None:
    orig = ValueError("nope")
    assert map_sentinel(orig, _TypedError, "C", 400, "d") is orig
