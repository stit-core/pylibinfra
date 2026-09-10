"""Порт аутентификации и интерсептор, который его применяет."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextvars import ContextVar, Token
from typing import Any, Protocol, runtime_checkable

import grpc

from pylibinfra.logkit import Logger

from .interceptors import HandlerInterceptor, echo_request_id, metadata_value

AUTHORIZATION_KEY = "authorization"
_BEARER_SCHEME = "bearer"
# Health нужен оркестратору до аутентификации, reflection обслуживает сам себя.
_EXEMPT_PREFIXES = ("/grpc.health.v1.Health/", "/grpc.reflection.")

_MISSING_TOKEN = "missing bearer token"  # noqa: S105 — текст отказа, не секрет
_REJECTED = "unauthenticated"

_identity: ContextVar[Any] = ContextVar("pylibinfra_grpc_identity", default=None)


@runtime_checkable
class Authenticator(Protocol):
    """Порт аутентификации: проверяет bearer-токен и возвращает identity вызывающего."""

    async def authenticate(self, token: str) -> Any:
        """Отдаёт identity по токену; любая ошибка — отказ (её текст клиенту не уходит)."""
        ...


def identity() -> Any:
    """Identity текущего вызова — то, что вернул `Authenticator`; `None` вне вызова и без auth."""
    return _identity.get()


class AuthInterceptor(HandlerInterceptor):
    """Требует bearer-токен, кладёт identity в контекст вызова; health и reflection — без auth."""

    def __init__(self, authenticator: Authenticator, log: Logger) -> None:
        self._authenticator = authenticator
        self._log = log

    async def call_unary(self, inner: Any, method: str, request: Any, context: Any) -> Any:
        """Аутентифицирует вызов до handler'а; отказ — `UNAUTHENTICATED` без деталей порта."""
        if _is_exempt(method):
            return await inner(request, context)
        token = await self._authenticate(method, context)
        try:
            return await inner(request, context)
        finally:
            _identity.reset(token)

    async def call_stream(
        self, inner: Any, method: str, request: Any, context: Any
    ) -> AsyncIterator[Any]:
        """То же для потока: identity живёт до закрытия стрима."""
        if _is_exempt(method):
            async for response in inner(request, context):
                yield response
            return
        token = await self._authenticate(method, context)
        try:
            async for response in inner(request, context):
                yield response
        finally:
            _identity.reset(token)

    async def _authenticate(self, method: str, context: Any) -> Token[Any]:
        token = _bearer_token(context)
        if not token:
            await _reject(context, _MISSING_TOKEN)
        try:
            who = await self._authenticator.authenticate(token)
        except Exception as exc:
            # Текст ошибки порта может нести внутренние детали (имя ключа, состояние
            # стора): оператору он нужен, клиенту — нет.
            self._log.warn("grpc auth rejected", grpc_method=method, error=str(exc) or repr(exc))
            await _reject(context, _REJECTED)
            raise  # недостижимо: abort поднимает AbortError
        return _identity.set(who)


async def _reject(context: Any, message: str) -> None:
    await echo_request_id(context)
    await context.abort(grpc.StatusCode.UNAUTHENTICATED, message)


def _is_exempt(method: str) -> bool:
    return method.startswith(_EXEMPT_PREFIXES)


def _bearer_token(context: Any) -> str:
    """Принимает и «Bearer <token>» (регистр префикса любой), и голый токен."""
    value = metadata_value(context, AUTHORIZATION_KEY).strip()
    head, _, rest = value.partition(" ")
    # Заголовок из одного префикса — это отсутствие токена, а не токен «Bearer»:
    # иначе порт зря дёргает свой стор (gRPC срезает хвостовой пробел значения).
    if head.lower() == _BEARER_SCHEME:
        return rest.strip()
    return value
