"""APIError — унифицированная error-модель ({code,status,title,detail}) в форме
RFC 7807 Problem+JSON. map_err/map_sentinel мапят доменные ошибки в APIError;
exception-handlers рендерят APIError → JSON-ответ с нужным HTTP-статусом.
"""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"


# APIError — Exception (а не pydantic BaseModel): BaseModel и Exception имеют
# несовместимый C-layout, совместить нельзя. Сериализация — ручная to_body
# (Problem+JSON omit-empty), схема pydantic тут не нужна.
class APIError(Exception):
    code: str
    status: int
    title: str
    detail: str
    type: str
    instance: str

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
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type
        self.instance = instance

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"

    def with_defaults(self) -> APIError:
        type_ = self.type or "about:blank"
        title = self.title
        if not title and self.status:
            title = _status_phrase(self.status)
        return APIError(
            code=self.code,
            status=self.status,
            title=title,
            detail=self.detail,
            type=type_,
            instance=self.instance,
        )

    def to_body(self) -> dict[str, object]:
        """Тело Problem+JSON с omit-empty: пустые строки и нулевой status не
        попадают в ответ (зеркалит `json:",omitempty"` в Go-APIError)."""
        body: dict[str, object] = {}
        fields: tuple[tuple[str, str | int], ...] = (
            ("code", self.code),
            ("status", self.status),
            ("title", self.title),
            ("detail", self.detail),
            ("type", self.type),
            ("instance", self.instance),
        )
        for key, value in fields:
            if value == "" or value == 0:
                continue
            body[key] = value
        return body


def _status_phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def error(code: str, status: int, detail: str) -> APIError:
    return APIError(code=code, status=status, title=_status_phrase(status), detail=detail)


def bad_request(code: str, detail: str) -> APIError:
    return error(code, HTTPStatus.BAD_REQUEST, detail)


def unauthorized(code: str, detail: str) -> APIError:
    return error(code, HTTPStatus.UNAUTHORIZED, detail)


def forbidden(code: str, detail: str) -> APIError:
    return error(code, HTTPStatus.FORBIDDEN, detail)


def not_found(code: str, detail: str) -> APIError:
    return error(code, HTTPStatus.NOT_FOUND, detail)


def conflict(code: str, detail: str) -> APIError:
    return error(code, HTTPStatus.CONFLICT, detail)


def internal(code: str, detail: str) -> APIError:
    return error(code, HTTPStatus.INTERNAL_SERVER_ERROR, detail)


class ErrorMapping:
    def __init__(
        self,
        match: Callable[[BaseException], bool],
        code: str,
        status: int,
        detail: str,
    ) -> None:
        self.match = match
        self.code = code
        self.status = status
        self.detail = detail


def map_err(err: BaseException | None, *mappings: ErrorMapping) -> BaseException | None:
    if err is None:
        return None
    for m in mappings:
        if m.match(err):
            return error(m.code, m.status, m.detail)
    return err


def map_sentinel(
    err: BaseException | None,
    target: type[BaseException] | BaseException,
    code: str,
    status: int,
    detail: str,
) -> BaseException | None:
    """Если err — экземпляр target (тип) или совпадает с target (sentinel-
    инстанс по identity/равенству, аналог errors.Is), мапит в APIError."""
    if err is None:
        return None
    if isinstance(target, type):
        if isinstance(err, target):
            return error(code, status, detail)
    elif err is target or err == target:
        return error(code, status, detail)
    return err


def _api_error_response(exc: APIError) -> JSONResponse:
    e = exc.with_defaults()
    status = e.status or HTTPStatus.INTERNAL_SERVER_ERROR
    return JSONResponse(
        status_code=status,
        content=e.to_body(),
        media_type=PROBLEM_CONTENT_TYPE,
    )


def register_error_handlers(app: FastAPI) -> None:
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        # exc сужен Starlette до Exception; на этом handler'е он всегда APIError.
        assert isinstance(exc, APIError)
        return _api_error_response(exc)

    app.add_exception_handler(APIError, handle)
