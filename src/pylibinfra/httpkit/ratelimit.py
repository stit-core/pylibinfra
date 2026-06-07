"""RateLimitConfig + RateLimitMiddleware: token-bucket per-IP (rps/burst).
Превышение → 429 (APIError). rps 0 → лимитер отключён.
"""

from __future__ import annotations

import json
import time

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.types import ASGIApp, Receive, Scope, Send

from .error import APIError, error


class RateLimitConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    rps: int = Field(default=100, validation_alias="HTTP_RATE_LIMIT_RPS")
    burst: int = Field(default=200, validation_alias="HTTP_RATE_LIMIT_BURST")


class _Bucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, tokens: float, updated: float) -> None:
        self.tokens = tokens
        self.updated = updated


def _too_many() -> APIError:
    return error("RATE_LIMITED", 429, "rate limit exceeded")


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, cfg: RateLimitConfig) -> None:
        self._app = app
        self._rps = float(cfg.rps)
        self._burst = float(cfg.burst if cfg.burst > 0 else cfg.rps)
        self._buckets: dict[str, _Bucket] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._rps <= 0:
            await self._app(scope, receive, send)
            return

        if not self._allow(_client_ip(scope)):
            await self._reject(send)
            return
        await self._app(scope, receive, send)

    def _allow(self, ip: str) -> bool:
        now = time.monotonic()
        bucket = self._buckets.get(ip)
        if bucket is None:
            bucket = _Bucket(self._burst, now)
            self._buckets[ip] = bucket
        bucket.tokens = min(self._burst, bucket.tokens + (now - bucket.updated) * self._rps)
        bucket.updated = now
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    async def _reject(self, send: Send) -> None:
        body = json.dumps(_too_many().with_defaults().to_body()).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [(b"content-type", b"application/problem+json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    if client:
        return str(client[0])
    return ""
