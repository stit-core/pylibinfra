"""Transactor: проброс открытой транзакции через contextvar (аналог проброса
tx в Go context). conn() выбирает соединение из contextvar, иначе сам пул."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Protocol, runtime_checkable

from .types import Connection, Pool, Record


# Структурный надтип Pool и Connection в части, которую использует conn()/DB.
@runtime_checkable
class Executor(Protocol):
    async def execute(self, query: str, *args: Any) -> str: ...
    async def fetch(self, query: str, *args: Any) -> list[Record]: ...
    async def fetchrow(self, query: str, *args: Any) -> Record | None: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...


@runtime_checkable
class Transactor(Protocol):
    async def with_tx[T](self, fn: Callable[[], Awaitable[T]]) -> T: ...


# Текущее tx-соединение для активного asyncio-таска; None — работаем через пул.
_current_conn: ContextVar[Connection | None] = ContextVar("pgkit_current_conn", default=None)


def conn(pool: Pool) -> Executor:
    c = _current_conn.get()
    if c is not None:
        return c  # type: ignore[no-any-return]
    return pool  # type: ignore[no-any-return]


def in_tx() -> bool:
    """True, если текущий asyncio-таск исполняется внутри Transactor.with_tx (есть
    захваченное tx-соединение). Нужно, например, outboxkit WithRequireTx, чтобы
    гарантировать атомарную запись события с бизнес-стейтом."""
    return _current_conn.get() is not None


class PoolTransactor:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def with_tx[T](self, fn: Callable[[], Awaitable[T]]) -> T:
        if _current_conn.get() is not None:
            return await fn()

        async with self._pool.acquire() as c, c.transaction():
            token = _current_conn.set(c)
            try:
                return await fn()
            finally:
                _current_conn.reset(token)
