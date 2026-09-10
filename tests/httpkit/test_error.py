from http import HTTPStatus

import pytest
from fastapi import FastAPI

from pylibinfra.httpkit import (
    PROBLEM_CONTENT_TYPE,
    APIError,
    ErrorMapping,
    bad_request,
    code_for_status,
    conflict,
    error,
    forbidden,
    internal,
    map_as,
    map_err,
    map_is,
    not_found,
    register_error_handlers,
    unauthorized,
    unavailable,
)
from pylibinfra.testkit.logtest import Capture

from .conftest import client, one


def test_error_builds_fields() -> None:
    body = error("TEST_CODE", HTTPStatus.BAD_REQUEST, "bad input").to_body()
    assert body == {
        "title": "Bad Request",
        "status": 400,
        "detail": "bad input",
        "code": "TEST_CODE",
    }


def test_to_body_omits_empty() -> None:
    body = APIError(code="X", status=400, type="https://errors.example/x").to_body()
    assert body == {"type": "https://errors.example/x", "status": 400, "code": "X"}
    assert "instance" not in body


def test_str_is_code_and_detail() -> None:
    assert str(error("C", 400, "d")) == "C: d"


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
        (unavailable, 503),
    ],
)
def test_status_builders(fn: object, code: int) -> None:
    assert fn("c", "d").status == code  # type: ignore[operator]


@pytest.mark.parametrize(
    ("status", "want"),
    [(500, "INTERNAL"), (501, "NOT_IMPLEMENTED"), (502, "BAD_GATEWAY"), (503, "UNAVAILABLE")],
)
def test_code_for_status_server(status: int, want: str) -> None:
    assert code_for_status(status) == want


def test_code_for_status_client() -> None:
    assert code_for_status(404) == "NOT_FOUND"
    assert code_for_status(599) == "INTERNAL"


_SENTINEL = RuntimeError("sentinel error")


class _TypedError(Exception):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


def test_map_err_none_returns_none() -> None:
    assert map_err(None) is None


def test_map_err_first_match_wins() -> None:
    mapped = map_err(
        _SENTINEL,
        ErrorMapping(lambda e: isinstance(e, ValueError), "VALUE", 400, "value"),
        ErrorMapping(lambda e: e is _SENTINEL, "SENTINEL", 404, "not found"),
    )
    assert isinstance(mapped, APIError)
    assert (mapped.code, mapped.status) == ("SENTINEL", 404)


def test_map_err_no_match_returns_original() -> None:
    original = ValueError("other")
    assert map_err(original) is original


def test_map_is_none_returns_none() -> None:
    assert map_is(None, _SENTINEL, "C", 404, "d") is None


def test_map_is_instance_and_type() -> None:
    assert isinstance(map_is(_SENTINEL, _SENTINEL, "S", 404, "d"), APIError)
    assert isinstance(map_is(_TypedError("email"), _TypedError, "T", 400, "d"), APIError)


def test_map_is_walks_cause_chain() -> None:
    try:
        try:
            raise _TypedError("email")
        except _TypedError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        mapped = map_is(outer, _TypedError, "T", 400, "d")
    assert isinstance(mapped, APIError)


def test_map_is_no_match_returns_original() -> None:
    original = ValueError("nope")
    assert map_is(original, _TypedError, "C", 400, "d") is original


def test_map_as_builds_from_matched_exception() -> None:
    mapped = map_as(
        _TypedError("email"), _TypedError, lambda e: bad_request("VALIDATION", f"bad {e.field}")
    )
    assert isinstance(mapped, APIError)
    assert mapped.detail == "bad email"


def test_map_as_none_and_no_match() -> None:
    assert map_as(None, _TypedError, lambda e: bad_request("V", "d")) is None
    original = ValueError("x")
    assert map_as(original, _TypedError, lambda e: bad_request("V", "d")) is original


def test_map_as_rejects_base_exception_types() -> None:
    with pytest.raises(ValueError, match="matches every error"):
        map_as(_SENTINEL, Exception, lambda e: bad_request("V", "d"))


def _app(log: Capture) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app, log)

    @app.get("/mapped")
    async def mapped() -> dict[str, str]:
        raise not_found("USER_NOT_FOUND", "no such user")

    @app.get("/leaky")
    async def leaky() -> dict[str, str]:
        raise internal("DB_DEAD", "dsn=postgres://user:pass@host/db")

    @app.post("/typed")
    async def typed(payload: dict[str, int]) -> dict[str, int]:
        return payload

    return app


async def test_api_error_renders_problem_json(log: Capture) -> None:
    async with client(_app(log)) as c:
        resp = await c.get("/mapped")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert resp.json()["code"] == "USER_NOT_FOUND"


async def test_5xx_is_neutralized_and_logged(log: Capture) -> None:
    async with client(_app(log)) as c:
        resp = await c.get("/leaky")
    assert resp.status_code == 500
    assert resp.json() == {
        "title": "Internal Server Error",
        "status": 500,
        "detail": "internal error",
        "code": "INTERNAL",
    }
    assert "postgres://" not in resp.text
    assert "postgres://" in one(log, "handler error").value("err")


async def test_statusless_error_is_neutralized_as_500(log: Capture) -> None:
    app = FastAPI()
    register_error_handlers(app, log)

    @app.get("/no-status")
    async def no_status() -> None:
        raise APIError(code="BOOM", detail="dsn=postgres://user:pass@db/app")

    async with client(app) as c:
        resp = await c.get("/no-status")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "internal error"
    assert "postgres://" not in resp.text
    assert "postgres://" in one(log, "handler error").value("err")


async def test_http_exception_becomes_problem_json(log: Capture) -> None:
    async with client(_app(log)) as c:
        resp = await c.get("/missing-route")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_validation_error_carries_locations(log: Capture) -> None:
    async with client(_app(log)) as c:
        resp = await c.post("/typed", json={"n": "not-a-number"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"]
    assert "n" in body["errors"][0]["location"]


def test_status_phrase_of_unknown_code_is_empty() -> None:
    from pylibinfra.httpkit.error import status_phrase

    assert status_phrase(200) == "OK"
    assert status_phrase(799) == ""
