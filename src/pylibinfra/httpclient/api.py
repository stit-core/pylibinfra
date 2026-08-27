"""api.py — типизированный JSON-слой поверх Client: декод тела (в т.ч. в wire-модель
через model=), маппинг статусов в typed-ошибки, Idempotency-Key. retry / circuit
breaker / OTel / X-Request-ID — внутри Client."""

from typing import Any, overload

import httpx
from pydantic import BaseModel, ValidationError

from .breaker import BreakerOpenError
from .client import Client

HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"
HEADER_RETRY_AFTER = "Retry-After"

# Мутации ретраятся только с Idempotency-Key: потерянный ответ + слепой повтор
# без ключа = двойное списание. GET/HEAD безопасны всегда.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Тело ошибки в ApiError — только сниппет: диагностика в логи, не транспорт мегабайт.
_BODY_SNIPPET_LIMIT = 256


class ApiError(Exception):
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
    """JSON-API поверх Client.

    Client сам райзит только 5xx-после-ретраев (HTTPStatusError); 4xx приходит
    Response'ом без исключения — поэтому статусы разбираются здесь.
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    async def aclose(self) -> None:
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
        retries = 0 if method.upper() in _MUTATING_METHODS and not idempotency_key else None
        try:
            resp = await self._client.request(
                method, path, json=json, params=params, headers=hdrs, retries=retries
            )
        except BreakerOpenError as exc:
            raise UnavailableError(
                f"{method} {path}: circuit breaker open", method=method, path=path
            ) from exc
        except httpx.HTTPStatusError as exc:
            # Client поднимает HTTPStatusError только на 5xx-после-ретраев.
            raise UnavailableError(
                f"{method} {path}: {exc}",
                method=method,
                path=path,
                status_code=exc.response.status_code,
                retry_after=_retry_after(exc.response),
                body_snippet=_snippet(exc.response),
            ) from exc
        except httpx.TransportError as exc:
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
    """Именованные ApiClient'ы (несколько независимых бэкендов в одном приложении);
    собирается ApiClientsProvider из BackendsConfig."""

    def __init__(self, clients: dict[str, ApiClient]) -> None:
        self._clients = dict(clients)

    def get(self, name: str) -> ApiClient:
        try:
            return self._clients[name]
        except KeyError:
            known = ", ".join(sorted(self._clients)) or "<none>"
            raise UnknownBackendError(
                f"httpclient: backend {name!r} is not configured (known: {known})"
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients))

    def __contains__(self, name: str) -> bool:
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
    ctx = f"{method} {req.url}: {resp.status_code}"
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
    # Только числовая форма заголовка; HTTP-date вариант не парсим — caller'у
    # достаточно «когда можно повторить» в секундах либо ничего.
    raw = resp.headers.get(HEADER_RETRY_AFTER)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
