"""IdempotencyConfig + IdempotencyStore Protocol + IdempotencyMiddleware.

Для POST/PUT/PATCH с заголовком Idempotency-Key: при попадании в store отдаёт
закэшированный (response, status) и инкрементит http_idempotency_total{result=replayed};
иначе выполняет handler, кэширует ответ и считает result=stored. Store
реализуется в cachekit (CacheIdempotencyStore) — сигнатура Protocol совпадает.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .metrics import HTTPInstruments

_MUTATING = frozenset({"POST", "PUT", "PATCH"})


class IdempotencyConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    header: str = Field(default="Idempotency-Key", validation_alias="HTTP_IDEMPOTENCY_HEADER")
    # секунды
    ttl: float = Field(default=86400.0, validation_alias="HTTP_IDEMPOTENCY_TTL")
    max_body_bytes: int = Field(default=65536, validation_alias="HTTP_IDEMPOTENCY_MAX_BODY_BYTES")


@runtime_checkable
class IdempotencyStore(Protocol):
    async def get(self, key: str) -> tuple[bytes | None, int, bool]: ...
    async def set(self, key: str, response: bytes, status: int) -> None: ...


class IdempotencyMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        cfg: IdempotencyConfig,
        store: IdempotencyStore,
        instruments: HTTPInstruments,
    ) -> None:
        self._app = app
        self._header = cfg.header.lower().encode("latin-1")
        self._store = store
        self._instr = instruments

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "") not in _MUTATING:
            await self._app(scope, receive, send)
            return

        key = _header(scope, self._header)
        if not key:
            await self._app(scope, receive, send)
            return

        cached, status, found = await self._store.get(key)
        if found and cached is not None:
            await _replay(send, cached, status)
            self._instr.idempotency_total.add(
                1, {"path": scope.get("path", ""), "result": "replayed"}
            )
            return

        captured = _Capture(send)
        await self._app(scope, receive, captured.send)
        await self._store.set(key, captured.body, captured.status)
        self._instr.idempotency_total.add(1, {"path": scope.get("path", ""), "result": "stored"})


class _Capture:
    def __init__(self, send: Send) -> None:
        self._send = send
        self.status = 200
        self.body = b""

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status = int(message["status"])
        elif message["type"] == "http.response.body":
            self.body += message.get("body", b"")
        await self._send(message)


async def _replay(send: Send, body: bytes, status: int) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return bytes(value).decode("latin-1")
    return ""
