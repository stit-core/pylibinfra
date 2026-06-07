"""tx-хелперы для pgkit: Identity (passthrough-Transactor), Recorder (считает
вызовы with_tx) и in_rollback_tx — прогон тела в реальной транзакции на пуле с
откатом после, чтобы интеграционный тест не оставлял мусор в БД."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable

from pylibinfra.pgkit.transactor import _current_conn
from pylibinfra.pgkit.types import Connection, Pool


class Identity:
    """Transactor без транзакции: просто вызывает fn (как Go testkit.Identity)."""

    async def with_tx[T](self, fn: Callable[[], Awaitable[T]]) -> T:
        return await fn()


class Recorder:
    """Transactor, считающий вызовы with_tx; тело исполняется без транзакции."""

    def __init__(self) -> None:
        self._calls = 0

    async def with_tx[T](self, fn: Callable[[], Awaitable[T]]) -> T:
        self._calls += 1
        return await fn()

    def calls(self) -> int:
        return self._calls


@contextlib.asynccontextmanager
async def in_rollback_tx(pool: Pool) -> AsyncIterator[Connection]:
    """Открывает транзакцию на pool, кладёт соединение в pgkit-contextvar (так DB
    и Transactor видят tx-conn) и в конце откатывает её — состояние БД после теста
    остаётся чистым."""
    async with pool.acquire() as c:
        conn: Connection = c
        tx = conn.transaction()
        await tx.start()
        token = _current_conn.set(conn)
        try:
            yield conn
        finally:
            _current_conn.reset(token)
            await tx.rollback()
