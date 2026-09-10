"""Аутентификация: контракт extractor'а, субъект идемпотентности и ASGI-гейт."""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar, Token
from typing import Protocol, runtime_checkable

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from pylibinfra.logkit import Logger, nop

from .error import APIError, HttpkitError, unauthorized, unavailable, write_error
from .response import tap

AUTH_DONE_KEY = "pylibinfra.httpkit.auth_done"
"""Ключ scope: credentials уже проверены выше по цепочке, повторно extractor не зовём."""

_subject: ContextVar[str] = ContextVar("pylibinfra_httpkit_subject", default="")


class AuthUnavailableError(HttpkitError):
    """Зависимость аутентификации недоступна (JWKS, session store): клиент получает 503, не 401."""


@runtime_checkable
class AuthExtractor(Protocol):
    """Проверяет credentials запроса и возвращает identity; ошибка — отказ в доступе.

    Ошибка, обёрнутая в `AuthUnavailableError`, даёт 503 `AUTH_UNAVAILABLE`; возвращённый
    `APIError` сохраняет свои статус и код; любая другая — 401 `UNAUTHORIZED`.
    """

    async def __call__(self, request: Request) -> object:
        """Возвращает identity запроса; identity кладётся в `request.state.auth`."""
        ...


def subject() -> str:
    """Идентификатор аутентифицированного субъекта текущего запроса; пусто — анонимный."""
    return _subject.get()


def bind_subject(value: str) -> Token[str]:
    """Кладёт субъект в контекст запроса — по нему scope'ится ключ идемпотентности."""
    return _subject.set(value)


def unbind_subject(token: Token[str]) -> None:
    """Восстанавливает контекст субъекта, каким он был до `bind_subject`."""
    _subject.reset(token)


def auth_error(exc: BaseException, log: Logger) -> APIError:
    """Переводит ошибку extractor'а в APIError; текст причины остаётся в логе."""
    if isinstance(exc, AuthUnavailableError) or isinstance(exc.__cause__, AuthUnavailableError):
        log.warn("auth backend unavailable", err=repr(exc))
        return unavailable("AUTH_UNAVAILABLE", "authentication backend is unavailable")
    if isinstance(exc, APIError):
        log.debug("auth rejected", err=repr(exc), code=exc.code)
        return exc
    log.debug("auth rejected", err=repr(exc))
    return unauthorized("UNAUTHORIZED", "unauthorized")


class AuthMiddleware:
    """Гейт: identity от extractor'а кладётся в `request.state.auth`, отказ — problem+json.

    Гейт deny-by-default: под ним все маршруты, кроме `public_paths` (`/health`, `/ready`,
    docs). Публичные маршруты сервиса объявляет extractor, возвращая анонимную identity.
    """

    def __init__(
        self,
        app: ASGIApp,
        extractor: AuthExtractor,
        log: Logger | None = None,
        public_paths: Iterable[str] = (),
    ) -> None:
        """`public_paths` — точные пути, которые гейт пропускает без обращения к extractor'у."""
        self._app = app
        self._extractor = extractor
        self._log = log if log is not None else nop()
        self._public = frozenset(p for p in public_paths if p)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Запрос, аутентифицированный выше по цепочке, повторно не проверяется."""
        if scope["type"] != "http" or scope.get("path", "") in self._public:
            await self._app(scope, receive, send)
            return
        if scope.get(AUTH_DONE_KEY):
            await self._app(scope, receive, send)
            return

        observed = tap(scope, send)
        token = bind_subject("")
        try:
            try:
                identity = await self._extractor(Request(scope, receive))
            except Exception as exc:
                await write_error(observed.send, auth_error(exc, self._log))
                return
            scope.setdefault("state", {})["auth"] = identity
            scope[AUTH_DONE_KEY] = True
            await self._app(scope, receive, observed.send)
        finally:
            unbind_subject(token)
