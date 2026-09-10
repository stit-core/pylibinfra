"""Token-bucket на клиента: конфиг, определение IP клиента и middleware."""

from __future__ import annotations

import ipaddress
from datetime import datetime
from http import HTTPStatus

from pydantic import Field, model_validator
from starlette.types import ASGIApp, Receive, Scope, Send

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings
from pylibinfra.clockkit import Clock, Real

from .error import error, write_error
from .metrics import Instruments, instruments
from .response import header_values, tap

IDLE_TTL = 300.0
SWEEP_EVERY = 60.0


class RateLimitConfig(KitSettings):
    """Token-bucket на IP клиента; `rps = 0` выключает лимитер."""

    model_config = configkit.settings_config()

    rps: int = Field(default=100, validation_alias="HTTP_RATE_LIMIT_RPS")
    burst: int = Field(default=200, validation_alias="HTTP_RATE_LIMIT_BURST")
    trust_proxy: bool = Field(default=False, validation_alias="HTTP_RATE_LIMIT_TRUST_PROXY")

    @model_validator(mode="after")
    def _check(self) -> RateLimitConfig:
        if self.rps < 0 or self.burst < 0:
            raise ValueError("rps and burst must be >= 0")
        return self


def _parsed(value: str) -> str:
    candidate = value.strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return ""
    return candidate


def client_ip(scope: Scope, trust_proxy: bool) -> str:
    """IP клиента для лимитера, аудита и антибрутфорса.

    При `trust_proxy` берётся **правое** значение **последнего** `X-Forwarded-For` — его дописал
    доверенный прокси, а всё левее прислал клиент; невалидное значение уводит на `X-Real-IP`,
    затем на адрес соединения. Топология подразумевает ровно один доверенный прокси.
    """
    if trust_proxy:
        forwarded = header_values(scope, "X-Forwarded-For")
        if forwarded and (ip := _parsed(forwarded[-1].rsplit(",", 1)[-1])):
            return ip
        real = header_values(scope, "X-Real-IP")
        if real and (ip := _parsed(real[-1])):
            return ip
    peer = scope.get("client")
    return str(peer[0]) if peer else ""


class _Bucket:
    __slots__ = ("last_seen", "tokens", "updated")

    def __init__(self, tokens: float, at: datetime) -> None:
        self.tokens = tokens
        self.updated = at
        self.last_seen = at


class RateLimiter:
    """Token-bucket на клиента; простаивающие bucket'ы выметаются, память не растёт от сканов."""

    def __init__(self, cfg: RateLimitConfig, clock: Clock | None = None) -> None:
        """`burst = 0` приравнивается к `rps`."""
        self._clock = clock if clock is not None else Real()
        self._rps = float(cfg.rps)
        self._burst = float(cfg.burst if cfg.burst > 0 else cfg.rps)
        self._buckets: dict[str, _Bucket] = {}
        self._last_sweep = self._clock.now()

    def allow(self, key: str) -> bool:
        """Списывает один токен; False — клиент исчерпал bucket."""
        now = self._clock.now()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._sweep(now)
            bucket = _Bucket(self._burst, now)
            self._buckets[key] = bucket
        elapsed = (now - bucket.updated).total_seconds()
        bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rps)
        bucket.updated = now
        bucket.last_seen = now
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def size(self) -> int:
        """Число живых bucket'ов — наблюдаемость вытеснения в тестах."""
        return len(self._buckets)

    def _sweep(self, now: datetime) -> None:
        if (now - self._last_sweep).total_seconds() < SWEEP_EVERY:
            return
        self._buckets = {
            key: bucket
            for key, bucket in self._buckets.items()
            if (now - bucket.last_seen).total_seconds() <= IDLE_TTL
        }
        self._last_sweep = now


class RateLimitMiddleware:
    """Отвечает 429 с `Retry-After: 1`, когда клиент исчерпал свой bucket."""

    def __init__(
        self,
        app: ASGIApp,
        cfg: RateLimitConfig,
        instr: Instruments | None = None,
        clock: Clock | None = None,
    ) -> None:
        """`rps = 0` делает middleware сквозным."""
        self._app = app
        self._enabled = cfg.rps > 0
        self._trust_proxy = cfg.trust_proxy
        self._limiter = RateLimiter(cfg, clock)
        self._instr = instr if instr is not None else instruments()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Отказ дешёвый: до чтения тела и до работы обработчика."""
        if scope["type"] != "http" or not self._enabled:
            await self._app(scope, receive, send)
            return

        if self._limiter.allow(client_ip(scope, self._trust_proxy)):
            await self._app(scope, receive, send)
            return

        observed = tap(scope, send)
        observed.set_header("Retry-After", "1")
        self._instr.rate_limited.add(1)
        await write_error(
            observed.send,
            error("RATE_LIMITED", HTTPStatus.TOO_MANY_REQUESTS, "rate limit exceeded"),
        )
