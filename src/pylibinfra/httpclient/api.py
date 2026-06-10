"""api.py — типизированный JSON-слой поверх Client: декод тела, маппинг статусов в
typed-ошибки, Idempotency-Key. retry / circuit breaker / OTel / X-Request-ID — внутри Client."""

from typing import Any

import httpx

from .breaker import BreakerOpenError
from .client import Client

HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"


class ApiError(Exception):
    """База typed-ошибок ApiClient."""


class NotFoundError(ApiError):
    """404 — ресурса нет."""


class ConflictError(ApiError):
    """409 — конфликт состояния (в т.ч. повтор идемпотентной мутации)."""


class UnavailableError(ApiError):
    """Бэкенд недоступен или нарушил контракт: 5xx-после-ретраев, transport-ошибка,
    открытый breaker, не-JSON 2xx-тело, прочие 4xx (400/401/403/422 — для caller'а
    это сбой контракта, деталь — в __cause__)."""


class ApiClient:
    """JSON-API поверх Client.

    Client сам райзит только 5xx-после-ретраев (HTTPStatusError); 4xx приходит
    Response'ом без исключения — поэтому статусы разбираются здесь.
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(
        self, path: str, *, json: Any | None = None, idempotency_key: str | None = None
    ) -> Any:
        return await self.request("POST", path, json=json, idempotency_key=idempotency_key)

    async def put(
        self, path: str, *, json: Any | None = None, idempotency_key: str | None = None
    ) -> Any:
        return await self.request("PUT", path, json=json, idempotency_key=idempotency_key)

    async def patch(
        self, path: str, *, json: Any | None = None, idempotency_key: str | None = None
    ) -> Any:
        return await self.request("PATCH", path, json=json, idempotency_key=idempotency_key)

    async def delete(self, path: str, *, idempotency_key: str | None = None) -> Any:
        return await self.request("DELETE", path, idempotency_key=idempotency_key)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        hdrs = httpx.Headers(headers)
        if idempotency_key:
            hdrs[HEADER_IDEMPOTENCY_KEY] = idempotency_key
        try:
            resp = await self._client.request(method, path, json=json, params=params, headers=hdrs)
        except BreakerOpenError as exc:
            raise UnavailableError(f"{method} {path}: circuit breaker open") from exc
        except httpx.HTTPStatusError as exc:
            raise UnavailableError(f"{method} {path}: {exc}") from exc
        except httpx.TransportError as exc:
            raise UnavailableError(f"{method} {path}: {exc}") from exc
        return _decode(resp)


class ApiClients:
    """Именованные ApiClient'ы (несколько независимых бэкендов в одном приложении);
    собирается ApiClientsProvider из BackendsConfig."""

    def __init__(self, clients: dict[str, ApiClient]) -> None:
        self._clients = dict(clients)

    def get(self, name: str) -> ApiClient:
        try:
            return self._clients[name]
        except KeyError:
            raise KeyError(f"httpclient: backend {name!r} is not configured") from None


def _decode(resp: httpx.Response) -> Any:
    req = resp.request
    ctx = f"{req.method} {req.url}: {resp.status_code}"
    if resp.is_success:
        if not resp.content:
            return None
        # 2xx с не-JSON телом (прокси/WAF отдал HTML) — контрактный сбой, не паника наверху
        try:
            return resp.json()
        except ValueError as exc:
            raise UnavailableError(f"{ctx}: non-JSON body") from exc
    if resp.status_code == 404:
        raise NotFoundError(ctx)
    if resp.status_code == 409:
        raise ConflictError(ctx)
    raise UnavailableError(ctx)
