"""Тестовое FastAPI-приложение с error-handler'ами httpkit, ASGI-клиент и ассерты по ответу."""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI

from pylibinfra.httpkit import register_error_handlers


def new_app(register: Callable[[FastAPI], None]) -> FastAPI:
    """FastAPI с обработчиками ошибок httpkit; `register` навешивает роуты тестируемого кода."""
    app = FastAPI()
    register_error_handlers(app)
    register(app)
    return app


@contextlib.asynccontextmanager
async def new_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """httpx-клиент поверх ASGI-транспорта: запрос идёт прямо в app, без сети и сокетов."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@dataclass(frozen=True, slots=True)
class APIErrorLocation:
    """Элемент `errors[]`: сообщение и место проблемы в запросе."""

    message: str = ""
    location: str = ""
    value: Any = None


@dataclass(frozen=True, slots=True)
class APIError:
    """Тело ошибки API: поля RFC 9457, `code` httpkit и `errors[]` ошибок валидации."""

    type: str = ""
    title: str = ""
    status: int = 0
    detail: str = ""
    instance: str = ""
    code: str = ""
    errors: list[APIErrorLocation] = field(default_factory=list)


def _locations(body: dict[str, Any]) -> list[APIErrorLocation]:
    """`errors[]` из problem+json httpkit либо `detail[]` дефолтного ответа FastAPI."""
    raw = body.get("errors")
    if not isinstance(raw, list):
        detail = body.get("detail")
        raw = detail if isinstance(detail, list) else []
    out: list[APIErrorLocation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        location = item.get("location")
        if location is None:
            location = ".".join(str(part) for part in item.get("loc", ()))
        out.append(
            APIErrorLocation(
                message=str(item.get("message", item.get("msg", ""))),
                location=str(location),
                value=item.get("value", item.get("input")),
            )
        )
    return out


def decode_api_error(resp: httpx.Response) -> APIError:
    """Разбирает тело ответа как ошибку API; не-объект — `AssertionError`."""
    body = resp.json()
    if not isinstance(body, dict):
        raise AssertionError(f"handlertest: api error body is not an object: {resp.text}")
    detail = body.get("detail")
    return APIError(
        type=str(body.get("type", "")),
        title=str(body.get("title", "")),
        status=int(body.get("status", 0) or 0),
        detail=detail if isinstance(detail, str) else "",
        instance=str(body.get("instance", "")),
        code=str(body.get("code", "")),
        errors=_locations(body),
    )


def assert_status(resp: httpx.Response, want: int) -> None:
    """Проверяет код ответа; расхождение — `AssertionError` с телом."""
    if resp.status_code != want:
        raise AssertionError(f"handlertest: status={resp.status_code} want {want} body={resp.text}")


def assert_api_error(resp: httpx.Response, status: int, code: str) -> None:
    """Проверяет код ответа и поле `code` тела ошибки."""
    assert_status(resp, status)
    got = decode_api_error(resp)
    if got.code != code:
        raise AssertionError(f"handlertest: api error code={got.code!r} want {code!r} (full={got})")


def assert_json(resp: httpx.Response, want: object) -> None:
    """Сверяет JSON-тело ответа с ожидаемым значением."""
    got = resp.json()
    if got != want:
        raise AssertionError(
            f"handlertest: json mismatch:\n got={json.dumps(got, sort_keys=True)}\n"
            f"want={json.dumps(want, sort_keys=True)}"
        )
