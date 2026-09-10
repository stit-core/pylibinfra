"""JSON-слой поверх `Client`: декод тела (в т.ч. в wire-модель), таксономия ошибок.

Retry, circuit breaker, редиректы, OTel и `X-Request-ID` — внутри `Client`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, overload

import httpx
from pydantic import BaseModel, ValidationError

from .client import HEADER_IDEMPOTENCY_KEY, Client
from .errors import (
    BreakerOpenError,
    CrossHostRedirectError,
    HttpClientError,
    TooManyRedirectsError,
)
from .metrics import redacted_url
from .retryafter import parse_retry_after

HEADER_RETRY_AFTER = "Retry-After"

# Тело ошибки в ApiError — только сниппет: диагностика в логи, не транспорт мегабайт.
_BODY_SNIPPET_LIMIT = 256

# Исходы, в которых бэкенда фактически нет: транспорт, исчерпанный таймаут
# попытки, разомкнутая цепь и нарушение политики редиректов.
_UNAVAILABLE_CAUSES = (
    BreakerOpenError,
    CrossHostRedirectError,
    TooManyRedirectsError,
    httpx.TransportError,
    TimeoutError,
)


class ApiError(HttpClientError):
    """База typed-ошибок ApiClient; несёт контекст вызова для логов и классификации."""

    def __init__(
        self,
        message: str,
        *,
        method: str = "",
        path: str = "",
        status_code: int | None = None,
        retry_after: float | None = None,
        body_snippet: str = "",
    ) -> None:
        super().__init__(message)
        self.method = method
        self.path = path
        self.status_code = status_code
        self.retry_after = retry_after
        self.body_snippet = body_snippet


class NotFoundError(ApiError):
    """404 — ресурса нет."""


class ConflictError(ApiError):
    """409 — конфликт состояния (в т.ч. повтор идемпотентной мутации)."""


class AuthError(ApiError):
    """401 — сервисный ключ/токен невалиден или протух; ретрай не поможет."""


class ForbiddenError(ApiError):
    """403 — аутентифицирован, но операция запрещена; ретрай не поможет."""


class InvalidRequestError(ApiError):
    """400/422 и прочие 4xx — бэкенд отверг сам запрос: баг вызывающего или дрейф
    контракта; ретрай не поможет."""


class UnavailableError(ApiError):
    """Бэкенд недоступен: transport-ошибка, открытый breaker, 5xx-после-ретраев."""


class RateLimitedError(UnavailableError):
    """429 — интенсивность превышена. Transient (подкласс UnavailableError);
    retry_after — из заголовка Retry-After, если бэкенд его отдал."""


class ContractError(UnavailableError):
    """Бэкенд нарушил контракт ответа: не-JSON 2xx-тело или несоответствие wire-модели
    (model=). Для caller'а неотличим от недоступности — подкласс UnavailableError;
    деталь несоответствия — в __cause__."""


class UnknownBackendError(KeyError):
    """Бэкенд не объявлен в BackendsConfig — ошибка конфигурации, не рантайма."""


class ApiClient:
    """JSON-API поверх `Client`.

    `Client` возвращает ответ с любым статусом (5xx после ретраев — тоже ответ),
    поэтому статусы разбираются здесь.
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    @property
    def client(self) -> Client:
        """Клиент, поверх которого работает JSON-слой."""
        return self._client

    async def aclose(self) -> None:
        """Закрывает нижележащий клиент."""
        await self._client.aclose()

    @overload
    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any: ...

    @overload
    async def get[M: BaseModel](
        self, path: str, *, params: dict[str, Any] | None = None, model: type[M]
    ) -> M: ...

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        model: type[BaseModel] | None = None,
    ) -> Any:
        """GET: тело как JSON либо разобранное в `model`; статус — typed-ошибка."""
        return await self._request("GET", path, params=params, model=model)

    @overload
    async def post(
        self, path: str, *, json: Any | None = None, idempotency_key: str | None = None
    ) -> Any: ...

    @overload
    async def post[M: BaseModel](
        self,
        path: str,
        *,
        json: Any | None = None,
        idempotency_key: str | None = None,
        model: type[M],
    ) -> M: ...

    async def post(
        self,
        path: str,
        *,
        json: Any | None = None,
        idempotency_key: str | None = None,
        model: type[BaseModel] | None = None,
    ) -> Any:
        """POST: без `idempotency_key` не ретраится — дубль на сервере дороже повтора."""
        return await self._request(
            "POST", path, json=json, idempotency_key=idempotency_key, model=model
        )

    @overload
    async def put(
        self, path: str, *, json: Any | None = None, idempotency_key: str | None = None
    ) -> Any: ...

    @overload
    async def put[M: BaseModel](
        self,
        path: str,
        *,
        json: Any | None = None,
        idempotency_key: str | None = None,
        model: type[M],
    ) -> M: ...

    async def put(
        self,
        path: str,
        *,
        json: Any | None = None,
        idempotency_key: str | None = None,
        model: type[BaseModel] | None = None,
    ) -> Any:
        """PUT: метод идемпотентен, повторяется по политике клиента."""
        return await self._request(
            "PUT", path, json=json, idempotency_key=idempotency_key, model=model
        )

    @overload
    async def patch(
        self, path: str, *, json: Any | None = None, idempotency_key: str | None = None
    ) -> Any: ...

    @overload
    async def patch[M: BaseModel](
        self,
        path: str,
        *,
        json: Any | None = None,
        idempotency_key: str | None = None,
        model: type[M],
    ) -> M: ...

    async def patch(
        self,
        path: str,
        *,
        json: Any | None = None,
        idempotency_key: str | None = None,
        model: type[BaseModel] | None = None,
    ) -> Any:
        """PATCH: без `idempotency_key` не ретраится."""
        return await self._request(
            "PATCH", path, json=json, idempotency_key=idempotency_key, model=model
        )

    @overload
    async def delete(self, path: str, *, idempotency_key: str | None = None) -> Any: ...

    @overload
    async def delete[M: BaseModel](
        self, path: str, *, idempotency_key: str | None = None, model: type[M]
    ) -> M: ...

    async def delete(
        self,
        path: str,
        *,
        idempotency_key: str | None = None,
        model: type[BaseModel] | None = None,
    ) -> Any:
        """DELETE: метод идемпотентен, повторяется по политике клиента."""
        return await self._request("DELETE", path, idempotency_key=idempotency_key, model=model)

    @overload
    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> Any: ...

    @overload
    async def request[M: BaseModel](
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        model: type[M],
    ) -> M: ...

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        model: type[BaseModel] | None = None,
    ) -> Any:
        """Произвольный метод: политику повтора выбирает `Client` по методу и ключу."""
        return await self._request(
            method,
            path,
            json=json,
            params=params,
            headers=headers,
            idempotency_key=idempotency_key,
            model=model,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        model: type[BaseModel] | None = None,
    ) -> Any:
        hdrs = httpx.Headers(headers)
        if idempotency_key:
            hdrs[HEADER_IDEMPOTENCY_KEY] = idempotency_key
        try:
            resp = await self._client.request(method, path, json=json, params=params, headers=hdrs)
        except _UNAVAILABLE_CAUSES as exc:
            raise UnavailableError(f"{method} {path}: {exc}", method=method, path=path) from exc
        raw = _decode(resp)
        if model is None:
            return raw
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            raise ContractError(
                f"{method} {path}: response does not match {model.__name__}",
                method=method,
                path=path,
                status_code=resp.status_code,
            ) from exc


class ApiClients:
    """Именованные `ApiClient`'ы: несколько независимых бэкендов в одном приложении.

    Собирается `ApiClientsProvider` из `BackendsConfig`.
    """

    def __init__(self, clients: dict[str, ApiClient]) -> None:
        self._clients = dict(clients)

    def get(self, name: str) -> ApiClient:
        """`ApiClient` бэкенда; необъявленное имя — `UnknownBackendError`."""
        try:
            return self._clients[name]
        except KeyError:
            known = ", ".join(sorted(self._clients)) or "<none>"
            raise UnknownBackendError(
                f"httpclient: backend {name!r} is not configured (known: {known})"
            ) from None

    def names(self) -> tuple[str, ...]:
        """Имена объявленных бэкендов, отсортированные."""
        return tuple(sorted(self._clients))

    def __contains__(self, name: str) -> bool:
        """Объявлен ли бэкенд с таким именем."""
        return name in self._clients


_STATUS_ERRORS: dict[int, type[ApiError]] = {
    401: AuthError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitedError,
}


def _decode(resp: httpx.Response) -> Any:
    req = resp.request
    method, path = req.method, req.url.path
    # URL без query и userinfo: ключ, переданный параметром, не должен осесть в тексте
    # исключения и уехать в лог консьюмера.
    ctx = f"{method} {redacted_url(req.url)}: {resp.status_code}"
    if resp.is_success:
        if not resp.content:
            return None
        # 2xx с не-JSON телом (прокси/WAF отдал HTML) — контрактный сбой, не паника наверху
        try:
            return resp.json()
        except ValueError as exc:
            raise ContractError(
                f"{ctx}: non-JSON body",
                method=method,
                path=path,
                status_code=resp.status_code,
                body_snippet=_snippet(resp),
            ) from exc
    err_cls = _STATUS_ERRORS.get(resp.status_code)
    if err_cls is None:
        err_cls = InvalidRequestError if resp.status_code < 500 else UnavailableError
    raise err_cls(
        ctx,
        method=method,
        path=path,
        status_code=resp.status_code,
        retry_after=_retry_after(resp),
        body_snippet=_snippet(resp),
    )


def _snippet(resp: httpx.Response) -> str:
    try:
        return resp.text[:_BODY_SNIPPET_LIMIT]
    except Exception:  # сниппет best-effort: битая кодировка не должна ронять маппинг ошибки
        return ""


def _retry_after(resp: httpx.Response) -> float | None:
    return parse_retry_after(resp.headers.get(HEADER_RETRY_AFTER, ""), datetime.now(UTC))
