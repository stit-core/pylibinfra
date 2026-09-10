"""Unit-тесты handlertest: сборка app + ASGI-запрос без сети (без Docker)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from pylibinfra.httpkit import APIError, internal, not_found
from pylibinfra.testkit.handlertest import (
    assert_api_error,
    assert_json,
    assert_status,
    decode_api_error,
    new_app,
    new_client,
)


def _register(app: FastAPI) -> None:
    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    @app.get("/missing")
    async def missing() -> dict[str, str]:
        raise not_found("USER_NOT_FOUND", "no such user")

    @app.get("/rich")
    async def rich() -> dict[str, str]:
        raise APIError(
            code="RICH",
            status=418,
            title="I'm a teapot",
            detail="short and stout",
            type="https://errors.example/rich",
            instance="/rich/1",
        )

    @app.get("/leaky")
    async def leaky() -> dict[str, str]:
        raise internal("DB", "dsn=postgres://user:pass@host")

    @app.post("/typed")
    async def typed(payload: dict[str, int]) -> dict[str, int]:
        return payload


async def test_ok_response() -> None:
    async with new_client(new_app(_register)) as client:
        resp = await client.get("/ping")
        assert_status(resp, 200)
        assert_json(resp, {"pong": "ok"})


async def test_api_error_response() -> None:
    async with new_client(new_app(_register)) as client:
        resp = await client.get("/missing")
        assert_api_error(resp, 404, "USER_NOT_FOUND")


async def test_decode_carries_all_problem_fields() -> None:
    async with new_client(new_app(_register)) as client:
        resp = await client.get("/rich")
    got = decode_api_error(resp)
    assert got.type == "https://errors.example/rich"
    assert got.title == "I'm a teapot"
    assert got.status == 418
    assert got.detail == "short and stout"
    assert got.instance == "/rich/1"
    assert got.code == "RICH"
    assert got.errors == []


async def test_decode_reads_validation_locations() -> None:
    async with new_client(new_app(_register)) as client:
        resp = await client.post("/typed", json={"n": "nope"})
    got = decode_api_error(resp)
    assert got.status == 422
    assert got.code == "VALIDATION_ERROR"
    assert got.errors
    assert "n" in got.errors[0].location
    assert got.errors[0].message


async def test_new_app_neutralizes_5xx() -> None:
    async with new_client(new_app(_register)) as client:
        resp = await client.get("/leaky")
    assert resp.status_code == 500
    assert "postgres://" not in resp.text
    assert decode_api_error(resp).code == "INTERNAL"


async def test_assert_helpers_report_mismatch() -> None:
    async with new_client(new_app(_register)) as client:
        ok = await client.get("/ping")
        missing = await client.get("/missing")

    with pytest.raises(AssertionError, match="status="):
        assert_status(ok, 404)
    with pytest.raises(AssertionError, match="json mismatch"):
        assert_json(ok, {"pong": "nope"})
    with pytest.raises(AssertionError, match="api error code="):
        assert_api_error(missing, 404, "OTHER")


async def test_decode_rejects_non_object_body() -> None:
    def register(app: FastAPI) -> None:
        @app.get("/list")
        async def listing() -> list[int]:
            return [1, 2]

    async with new_client(new_app(register)) as client:
        resp = await client.get("/list")
    with pytest.raises(AssertionError, match="not an object"):
        decode_api_error(resp)


async def test_decode_skips_non_object_locations() -> None:
    from fastapi import FastAPI, HTTPException

    from pylibinfra.testkit.handlertest import new_client

    app = FastAPI()

    @app.get("/plain-detail")
    async def plain_detail() -> None:
        raise HTTPException(status_code=422, detail=["first", "second"])

    async with new_client(app) as client:
        resp = await client.get("/plain-detail")

    # detail списком строк — не список проблем: декод не падает и не выдумывает локации
    got = decode_api_error(resp)
    assert got.errors == []
    assert got.detail == ""


async def test_decode_reads_fastapi_default_validation_body() -> None:
    from fastapi import FastAPI

    from pylibinfra.testkit.handlertest import new_client

    app = FastAPI()

    @app.post("/raw")
    async def raw(payload: dict[str, int]) -> dict[str, int]:
        return payload

    async with new_client(app) as client:
        resp = await client.post("/raw", json={"n": "nope"})

    got = decode_api_error(resp)
    assert got.status == 0
    assert got.errors
    assert "n" in got.errors[0].location
    assert got.errors[0].message
