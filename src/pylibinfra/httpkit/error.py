"""APIError (RFC 9457 problem+json), конструкторы по статусам, мапперы доменных ошибок."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import Send

from pylibinfra.logkit import Logger, nop

from .response import BODY_OVERFLOW_KEY

PROBLEM_CONTENT_TYPE = "application/problem+json"

_MAX_CAUSE_DEPTH = 32

_SERVER_CODES: dict[int, str] = {
    501: "NOT_IMPLEMENTED",
    502: "BAD_GATEWAY",
    503: "UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


class HttpkitError(Exception):
    """База нетранспортных ошибок кита (кроме `ConfigError`)."""


# APIError — Exception, а не pydantic-модель: BaseModel и Exception имеют
# несовместимый C-layout.
class APIError(HttpkitError):
    """Ошибка API: тело problem+json `{type,title,status,detail,instance,code}` и HTTP-статус."""

    def __init__(
        self,
        *,
        code: str = "",
        status: int = 0,
        title: str = "",
        detail: str = "",
        type: str = "",
        instance: str = "",
    ) -> None:
        """Собирает ошибку; пустые поля в тело ответа не попадают."""
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type
        self.instance = instance

    def __str__(self) -> str:
        """`<code>: <detail>` — то же, что даёт Error() в golibinfra."""
        return f"{self.code}: {self.detail}"

    def to_body(self) -> dict[str, Any]:
        """Тело problem+json; пустые строки и нулевой status опускаются."""
        fields: tuple[tuple[str, str | int], ...] = (
            ("type", self.type),
            ("title", self.title),
            ("status", self.status),
            ("detail", self.detail),
            ("instance", self.instance),
            ("code", self.code),
        )
        return {key: value for key, value in fields if value != "" and value != 0}


def status_phrase(status: int) -> str:
    """Стандартный reason-phrase статуса; пустая строка для нестандартного кода."""
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def code_for_status(status: int) -> str:
    """Код ошибки по статусу для ответов, которые кит формирует сам (5xx → `INTERNAL` и родня)."""
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return _SERVER_CODES.get(status, "INTERNAL")
    phrase = status_phrase(status)
    return phrase.upper().replace(" ", "_").replace("-", "_") if phrase else "ERROR"


def error(code: str, status: int, detail: str) -> APIError:
    """APIError с указанным кодом, статусом и detail; title — reason-phrase статуса."""
    return APIError(code=code, status=status, title=status_phrase(status), detail=detail)


def bad_request(code: str, detail: str) -> APIError:
    """APIError со статусом 400."""
    return error(code, HTTPStatus.BAD_REQUEST, detail)


def unauthorized(code: str, detail: str) -> APIError:
    """APIError со статусом 401."""
    return error(code, HTTPStatus.UNAUTHORIZED, detail)


def forbidden(code: str, detail: str) -> APIError:
    """APIError со статусом 403."""
    return error(code, HTTPStatus.FORBIDDEN, detail)


def not_found(code: str, detail: str) -> APIError:
    """APIError со статусом 404."""
    return error(code, HTTPStatus.NOT_FOUND, detail)


def conflict(code: str, detail: str) -> APIError:
    """APIError со статусом 409."""
    return error(code, HTTPStatus.CONFLICT, detail)


def internal(code: str, detail: str) -> APIError:
    """APIError со статусом 500."""
    return error(code, HTTPStatus.INTERNAL_SERVER_ERROR, detail)


def unavailable(code: str, detail: str) -> APIError:
    """APIError со статусом 503."""
    return error(code, HTTPStatus.SERVICE_UNAVAILABLE, detail)


class ErrorMapping:
    """Одно правило `map_err`: предикат по исключению и результирующий APIError."""

    def __init__(
        self,
        match: Callable[[BaseException], bool],
        code: str,
        status: int,
        detail: str,
    ) -> None:
        """Правило: `match` решает, применимо ли оно; остальное — поля APIError."""
        self.match = match
        self.code = code
        self.status = status
        self.detail = detail


def _chain(err: BaseException) -> Iterator[BaseException]:
    """Цепочка `raise ... from ...`: сама ошибка и её `__cause__` до `_MAX_CAUSE_DEPTH`."""
    seen: set[int] = set()
    current: BaseException | None = err
    for _ in range(_MAX_CAUSE_DEPTH):
        if current is None or id(current) in seen:
            return
        seen.add(id(current))
        yield current
        current = current.__cause__


def map_err(err: BaseException | None, *mappings: ErrorMapping) -> BaseException | None:
    """Переводит ошибку в APIError по первому совпавшему правилу; остальные отдаёт как есть."""
    if err is None:
        return None
    for mapping in mappings:
        if mapping.match(err):
            return error(mapping.code, mapping.status, mapping.detail)
    return err


def map_is(
    err: BaseException | None,
    target: type[BaseException] | BaseException,
    code: str,
    status: int,
    detail: str,
) -> BaseException | None:
    """Мапит ошибку, совпавшую с `target` (тип или sentinel-инстанс) в цепочке `__cause__`."""
    if err is None:
        return None
    for link in _chain(err):
        if isinstance(target, type):
            if isinstance(link, target):
                return error(code, status, detail)
        elif link is target or link == target:
            return error(code, status, detail)
    return err


def map_as[E: BaseException](
    err: BaseException | None,
    exc_type: type[E],
    fn: Callable[[E], APIError],
) -> BaseException | None:
    """Мапит первую ошибку типа `exc_type` в цепочке через `fn`; `Exception` как тип запрещён."""
    if exc_type in (Exception, BaseException):
        raise ValueError("httpkit: map_as with Exception matches every error; use a concrete type")
    if err is None:
        return None
    for link in _chain(err):
        if isinstance(link, exc_type):
            return fn(link)
    return err


def neutralize(err: APIError, log: Logger) -> APIError:
    """Прячет текст 5xx от клиента (причина — в лог `handler error`); 4xx отдаёт как есть.

    Ошибка без статуса отвечает 500 (`api_error_response`), поэтому и нейтрализуется как 500 —
    иначе её detail уходил бы клиенту вместе с пятисоткой.
    """
    status = err.status or int(HTTPStatus.INTERNAL_SERVER_ERROR)
    if status < HTTPStatus.INTERNAL_SERVER_ERROR:
        return err
    log.error("handler error", status=status, code=err.code, err=str(err))
    return error(code_for_status(status), status, "internal error")


def api_error_response(err: APIError) -> JSONResponse:
    """Ответ problem+json по APIError; нулевой статус трактуется как 500."""
    status = err.status or int(HTTPStatus.INTERNAL_SERVER_ERROR)
    return JSONResponse(status_code=status, content=err.to_body(), media_type=PROBLEM_CONTENT_TYPE)


async def write_error(send: Send, err: APIError) -> None:
    """Пишет problem+json прямо в ASGI-`send` — для middleware, которым недоступен FastAPI."""
    body = json.dumps(err.to_body()).encode()
    await send(
        {
            "type": "http.response.start",
            "status": err.status or int(HTTPStatus.INTERNAL_SERVER_ERROR),
            "headers": [
                (b"content-type", PROBLEM_CONTENT_TYPE.encode()),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _validation_body(exc: RequestValidationError) -> dict[str, Any]:
    """Тело 422: problem+json плюс `errors[]` с местом и сообщением каждой проблемы."""
    body = error(
        "VALIDATION_ERROR", HTTPStatus.UNPROCESSABLE_ENTITY, "request validation failed"
    ).to_body()
    body["errors"] = [
        {
            "message": str(item.get("msg", "")),
            "location": ".".join(str(part) for part in item.get("loc", ())),
            "value": item.get("input"),
        }
        for item in exc.errors()
    ]
    return body


def register_error_handlers(app: FastAPI, log: Logger | None = None) -> None:
    """Ставит обработчики APIError, HTTPException и ошибок валидации; все отвечают problem+json.

    Нейтрализация 5xx (тело `{"code":"INTERNAL",…,"detail":"internal error"}`, причина — в лог
    `handler error`) не зависит от `log`: без логгера теряется только текст причины.
    """
    logger = log if log is not None else nop()

    async def on_api_error(_: Request, exc: Exception) -> Response:
        assert isinstance(exc, APIError)
        return api_error_response(neutralize(exc, logger))

    async def on_http_exception(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, StarletteHTTPException)
        # Тело, отбитое MaxBody, фреймворк успевает превратить в свою 400 — статус вернуть.
        if request.scope.get(BODY_OVERFLOW_KEY):
            return api_error_response(
                error(
                    "PAYLOAD_TOO_LARGE",
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "request body too large",
                )
            )
        detail = exc.detail if isinstance(exc.detail, str) else status_phrase(exc.status_code)
        api = error(code_for_status(exc.status_code), exc.status_code, detail)
        response = api_error_response(neutralize(api, logger))
        for name, value in (exc.headers or {}).items():
            response.headers[name] = value
        return response

    async def on_validation_error(_: Request, exc: Exception) -> Response:
        assert isinstance(exc, RequestValidationError)
        return JSONResponse(
            status_code=int(HTTPStatus.UNPROCESSABLE_ENTITY),
            content=_validation_body(exc),
            media_type=PROBLEM_CONTENT_TYPE,
        )

    app.add_exception_handler(APIError, on_api_error)
    app.add_exception_handler(StarletteHTTPException, on_http_exception)
    app.add_exception_handler(RequestValidationError, on_validation_error)
