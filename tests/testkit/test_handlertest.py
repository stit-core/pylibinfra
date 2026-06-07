"""Unit-тесты handlertest: сборка app + ASGI-запрос без сети (без Docker)."""

from __future__ import annotations

from fastapi import FastAPI

from pylibinfra.httpkit import not_found
from pylibinfra.testkit.handlertest import (
    assert_api_error,
    assert_json,
    assert_status,
    new_app,
    new_client,
)


def _register(app: FastAPI) -> None:
    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    @app.get("/missing")
    async def missing() -> dict[str, str]:
        raise not_found("user_not_found", "no such user")


async def test_ok_response() -> None:
    app = new_app(_register)
    async with new_client(app) as client:
        resp = await client.get("/ping")
        assert_status(resp, 200)
        assert_json(resp, {"pong": "ok"})


async def test_api_error_response() -> None:
    app = new_app(_register)
    async with new_client(app) as client:
        resp = await client.get("/missing")
        assert_api_error(resp, 404, "user_not_found")
