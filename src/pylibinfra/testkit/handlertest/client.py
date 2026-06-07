"""handlertest — хелперы для httpkit: собрать тестовое FastAPI-приложение с
error-handler'ами httpkit и httpx ASGI-клиент (запрос без сети), плюс ассерты по
APIError-ответу (Problem+JSON)."""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import httpx
from fastapi import FastAPI

from pylibinfra.httpkit import register_error_handlers


def new_app(register: Callable[[FastAPI], None]) -> FastAPI:
    """Минимальное FastAPI-приложение с httpkit error-handler'ами; register
    навешивает роуты тестируемого консьюмера."""
    app = FastAPI()
    register_error_handlers(app)
    register(app)
    return app


@contextlib.asynccontextmanager
async def new_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """httpx-клиент поверх ASGI-транспорта: бьёт прямо в app без сети/сокетов."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@dataclass(frozen=True, slots=True)
class APIError:
    code: str = ""
    status: int = 0
    title: str = ""
    detail: str = ""


def decode_api_error(resp: httpx.Response) -> APIError:
    """Достаёт APIError из Problem+JSON-тела (httpkit рендерит плоский объект)."""
    body = resp.json()
    if not isinstance(body, dict):
        raise AssertionError(f"handlertest: api error body is not an object: {resp.text}")
    return APIError(
        code=str(body.get("code", "")),
        status=int(body.get("status", 0)),
        title=str(body.get("title", "")),
        detail=str(body.get("detail", "")),
    )


def assert_status(resp: httpx.Response, want: int) -> None:
    if resp.status_code != want:
        raise AssertionError(f"status={resp.status_code} want {want} body={resp.text}")


def assert_api_error(resp: httpx.Response, status: int, code: str) -> None:
    assert_status(resp, status)
    got = decode_api_error(resp)
    if got.code != code:
        raise AssertionError(f"api error code={got.code!r} want {code!r} (full={got})")


def assert_json(resp: httpx.Response, want: object) -> None:
    got = resp.json()
    if got != want:
        raise AssertionError(
            f"json mismatch:\n got={json.dumps(got, sort_keys=True)}\n"
            f"want={json.dumps(want, sort_keys=True)}"
        )
